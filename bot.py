import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv


load_dotenv()

DATA_PATH = Path("stats.json")
DEFAULT_TIMEZONE = os.environ.get("TIMEZONE", "UTC")
SCRIM_CHANNEL_ID_ENV = os.environ.get("SCRIM_CHANNEL_ID")
RESULTS_CHANNEL_ID_ENV = os.environ.get("RESULTS_CHANNEL_ID")
COMMAND_ROLE_IDS = {
    int(role_id)
    for role_id in (
        os.environ.get("COMMAND_ROLE_ID_1"),
        os.environ.get("COMMAND_ROLE_ID_2"),
    )
    if role_id and role_id.isdigit()
}

NA_TIMEZONES: Dict[str, str] = {
    "EST": "America/New_York",
    "CST": "America/Chicago",
    "PST": "America/Los_Angeles",
}


@dataclass
class TeamRecord:
    name: str
    wins: int = 0
    losses: int = 0

    def as_dict(self) -> Dict[str, object]:
        return {"name": self.name, "wins": self.wins, "losses": self.losses}

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "TeamRecord":
        return cls(
            name=str(data.get("name", "Unknown Team")),
            wins=int(data.get("wins", 0)),
            losses=int(data.get("losses", 0)),
        )


@dataclass
class Match:
    match_id: int
    team: str
    display_time: str
    time_iso: Optional[str]
    timezone: Optional[str] = None
    channel_id: Optional[int] = None
    message_id: Optional[int] = None
    thread_id: Optional[int] = None
    interested_user_ids: List[int] = field(default_factory=list)
    reminder_sent: bool = False
    status: Literal["open", "closed"] = "open"
    outcome: Optional[Literal["win", "loss"]] = None
    score: Optional[str] = None

    def as_dict(self) -> Dict[str, object]:
        return {
            "match_id": self.match_id,
            "team": self.team,
            "display_time": self.display_time,
            "time_iso": self.time_iso,
            "timezone": self.timezone,
            "channel_id": self.channel_id,
            "message_id": self.message_id,
            "thread_id": self.thread_id,
            "interested_user_ids": self.interested_user_ids,
            "reminder_sent": self.reminder_sent,
            "status": self.status,
            "outcome": self.outcome,
            "score": self.score,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "Match":
        return cls(
            match_id=int(data.get("match_id", 0)),
            team=str(data.get("team", "Unknown")),
            display_time=str(data.get("display_time", "Unknown")),
            time_iso=str(data["time_iso"]) if data.get("time_iso") else None,
            timezone=str(data["timezone"]) if data.get("timezone") else None,
            channel_id=int(data["channel_id"]) if data.get("channel_id") else None,
            message_id=int(data["message_id"]) if data.get("message_id") else None,
            thread_id=int(data["thread_id"]) if data.get("thread_id") else None,
            interested_user_ids=[int(user_id) for user_id in data.get("interested_user_ids", [])],
            reminder_sent=bool(data.get("reminder_sent", False)),
            status=str(data.get("status", "open")),
            outcome=str(data["outcome"]) if data.get("outcome") else None,  # type: ignore[arg-type]
            score=str(data["score"]) if data.get("score") else None,
        )


class StatsStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.teams: Dict[str, TeamRecord] = {}
        self.matches: List[Match] = []
        self.next_match_id = 1
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            content = json.loads(self.path.read_text())
        except json.JSONDecodeError:
            return

        if not isinstance(content, dict):
            return

        teams_data = content.get("teams", {}) if isinstance(content, dict) else {}
        for key, entry in teams_data.items():
            self.teams[key] = TeamRecord.from_dict(entry)

        matches_data = content.get("matches", [])
        if isinstance(matches_data, list):
            for entry in matches_data:
                if isinstance(entry, dict):
                    self.matches.append(Match.from_dict(entry))

        self.next_match_id = int(content.get("next_match_id", len(self.matches) + 1))

    def _save(self) -> None:
        payload = {
            "teams": {k: v.as_dict() for k, v in self.teams.items()},
            "matches": [m.as_dict() for m in self.matches],
            "next_match_id": self.next_match_id,
        }
        self.path.write_text(json.dumps(payload, indent=2))

    def _normalize_key(self, team_name: str) -> str:
        return team_name.strip().lower()

    def record_result(self, team_name: str, outcome: Literal["win", "loss"]) -> None:
        key = self._normalize_key(team_name)
        record = self.teams.get(key) or TeamRecord(name=team_name.strip())
        record.name = team_name.strip() or record.name
        if outcome == "win":
            record.wins += 1
        else:
            record.losses += 1
        self.teams[key] = record
        self._save()

    def totals(self) -> Tuple[int, int]:
        wins = sum(team.wins for team in self.teams.values())
        losses = sum(team.losses for team in self.teams.values())
        return wins, losses

    def list_by_outcome(self, outcome: Literal["win", "loss"]) -> List[Tuple[str, int]]:
        entries: List[Tuple[str, int]] = []
        for record in self.teams.values():
            count = record.wins if outcome == "win" else record.losses
            if count > 0:
                entries.append((record.name, count))
        entries.sort(key=lambda item: item[0].lower())
        return entries

    def add_match(self, team: str, display_time: str, time_iso: Optional[str], timezone: Optional[str]) -> Match:
        match = Match(
            match_id=self.next_match_id,
            team=team.strip(),
            display_time=display_time,
            time_iso=time_iso,
            timezone=timezone,
        )
        self.matches.append(match)
        self.next_match_id += 1
        self._save()
        return match

    def link_message(self, match_id: int, channel_id: int, message_id: int) -> None:
        match = self.get_match(match_id)
        if not match:
            return
        match.channel_id = channel_id
        match.message_id = message_id
        self._save()

    def link_thread(self, match_id: int, thread_id: int) -> None:
        match = self.get_match(match_id)
        if not match:
            return
        match.thread_id = thread_id
        self._save()

    def add_participant(self, match_id: int, user_id: int) -> None:
        match = self.get_match(match_id)
        if not match or match.status != "open":
            return
        if user_id not in match.interested_user_ids:
            match.interested_user_ids.append(user_id)
            self._save()

    def mark_reminder_sent(self, match_id: int) -> None:
        match = self.get_match(match_id)
        if not match:
            return
        match.reminder_sent = True
        self._save()

    def cancel_match(self, match_id: int) -> Optional[Match]:
        for index, match in enumerate(self.matches):
            if match.match_id == match_id and match.status == "open":
                removed = self.matches.pop(index)
                self._save()
                return removed
        return None

    def delete_match(self, match_id: int) -> Optional[Match]:
        for index, match in enumerate(self.matches):
            if match.match_id == match_id:
                removed = self.matches.pop(index)
                self._save()
                return removed
        return None

    def delete_all_matches(self) -> int:
        count = len(self.matches)
        if count:
            self.matches.clear()
            self._save()
        return count

    def list_open_matches(self) -> List[Match]:
        return [m for m in self.matches if m.status == "open"]

    def get_match(self, match_id: int) -> Optional[Match]:
        for match in self.matches:
            if match.match_id == match_id:
                return match
        return None

    def close_match(self, match_id: int, outcome: Literal["win", "loss"], score: str) -> Optional[Match]:
        match = self.get_match(match_id)
        if match is None or match.status != "open":
            return None
        match.status = "closed"
        match.outcome = outcome
        match.score = score
        self.record_result(match.team, outcome)
        self._save()
        return match

    def update_match_time(
        self,
        match_id: int,
        display_time: str,
        time_iso: Optional[str],
        timezone: Optional[str],
    ) -> Optional[Match]:
        match = self.get_match(match_id)
        if match is None or match.status != "open":
            return None
        match.display_time = display_time
        match.time_iso = time_iso
        match.timezone = timezone
        match.reminder_sent = False
        self._save()
        return match


class StatsView(discord.ui.View):
    def __init__(self, store: StatsStore) -> None:
        super().__init__(timeout=None)
        self.store = store

    @discord.ui.button(label="Show wins", style=discord.ButtonStyle.success, custom_id="stats_show_wins")
    async def show_wins(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[override]
        await self._send_outcome(interaction, "win")

    @discord.ui.button(label="Show losses", style=discord.ButtonStyle.danger, custom_id="stats_show_losses")
    async def show_losses(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[override]
        await self._send_outcome(interaction, "loss")

    async def _send_outcome(self, interaction: discord.Interaction, outcome: Literal["win", "loss"]) -> None:
        pairs = self.store.list_by_outcome(outcome)
        title = "Wins" if outcome == "win" else "Losses"
        if not pairs:
            message = f"No {title.lower()} recorded yet."
        else:
            lines = []
            for name, count in pairs:
                label = f"{name} ({count})" if count > 1 else name
                lines.append(label)
            message = "\n".join(lines)
        await interaction.response.send_message(message, ephemeral=True)


class SingleRemovalView(discord.ui.View):
    def __init__(self, store: StatsStore, author_id: int) -> None:
        super().__init__(timeout=300)
        self.store = store
        self.author_id = author_id

        options: List[discord.SelectOption] = []
        for match in self.store.matches:
            status = match.outcome if match.outcome else match.status
            description = (
                f"{status.title()} at {match.display_time}"
                if match.status != "open"
                else f"Open at {match.display_time}"
            )
            label = f"vs {match.team}"
            options.append(discord.SelectOption(label=label[:100], value=str(match.match_id), description=description[:100]))

        if not options:
            options.append(discord.SelectOption(label="No scrims available", value="none", default=True))

        select = discord.ui.Select(placeholder="Choose a scrim to remove", options=options, min_values=1, max_values=1)
        select.callback = self._on_select  # type: ignore[assignment]
        self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Only the command user can remove scrims.", ephemeral=True)
            return False
        return True

    async def _on_select(self, interaction: discord.Interaction) -> None:
        selected = interaction.data.get("values", []) if interaction.data else []  # type: ignore[assignment]
        if not selected:
            await interaction.response.send_message("No scrim selected.", ephemeral=True)
            return

        value = selected[0]
        if value == "none":
            await interaction.response.send_message("There are no scrims to remove.", ephemeral=True)
            return

        if not str(value).isdigit():
            await interaction.response.send_message("Invalid selection.", ephemeral=True)
            return

        removed = self.store.delete_match(int(value))
        if removed is None:
            await interaction.response.send_message("Scrim not found.", ephemeral=True)
            return

        await interaction.response.send_message(
            f"Removed scrim vs {removed.team}.", ephemeral=True
        )


class ScrimManagerView(discord.ui.View):
    def __init__(self, store: StatsStore, author_id: int) -> None:
        super().__init__(timeout=300)
        self.store = store
        self.author_id = author_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Only the command user can manage scrims.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Remove all", style=discord.ButtonStyle.danger)
    async def remove_all(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[override]
        removed_count = self.store.delete_all_matches()
        await interaction.response.send_message(
            f"Removed {removed_count} scrim(s) from the database." if removed_count else "No scrims to remove.",
            ephemeral=True,
        )

    @discord.ui.button(label="Remove single", style=discord.ButtonStyle.secondary)
    async def remove_single(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[override]
        view = SingleRemovalView(self.store, self.author_id)
        await interaction.response.send_message("Select a scrim to remove.", view=view, ephemeral=True)


def resolve_timezone_name(timezone_key: Optional[str]) -> str:
    if timezone_key and timezone_key in NA_TIMEZONES:
        return NA_TIMEZONES[timezone_key]
    return DEFAULT_TIMEZONE


def parse_day_time(
    day_time: str, timezone_name: Optional[str], meridiem: Optional[str] = None
) -> Tuple[str, Optional[datetime]]:
    """Parse `DD HH:MM` into a datetime in the supplied timezone.

    When `meridiem` is supplied ("AM" or "PM"), the hour component is interpreted
    as 12-hour time and converted to 24-hour time for scheduling.
    """

    parts = day_time.strip().split()
    if len(parts) != 2:
        return day_time, None

    try:
        day = int(parts[0])
        hour_minute = parts[1].split(":")
        hour = int(hour_minute[0])
        minute = int(hour_minute[1]) if len(hour_minute) > 1 else 0
    except (ValueError, IndexError):
        return day_time, None

    if meridiem:
        meridiem_upper = meridiem.upper()
        if meridiem_upper == "AM" and hour == 12:
            hour = 0
        elif meridiem_upper == "PM" and hour != 12:
            hour += 12

    try:
        tz = ZoneInfo(timezone_name or DEFAULT_TIMEZONE)
    except Exception:
        tz = None

    now = datetime.now(tz=tz)
    try:
        candidate = datetime(now.year, now.month, day, hour, minute, tzinfo=tz)
    except ValueError:
        return day_time, None

    if candidate < now:
        # Move to the next month when the date has already passed.
        month = candidate.month + 1
        year = candidate.year + (1 if month == 13 else 0)
        month = 1 if month == 13 else month
        try:
            candidate = candidate.replace(year=year, month=month)
        except ValueError:
            candidate = None

    return day_time, candidate


def timezone_label(timezone_name: Optional[str], parsed: Optional[datetime]) -> str:
    if parsed and parsed.tzinfo and parsed.tzname():
        return parsed.tzname() or DEFAULT_TIMEZONE
    if timezone_name:
        for label, zone in NA_TIMEZONES.items():
            if timezone_name in (label, zone):
                return label
        return timezone_name
    return DEFAULT_TIMEZONE


def create_timestamp(
    day_time: str, timezone_name: Optional[str], meridiem: Optional[str] = None
) -> Tuple[str, str, Optional[str], str]:
    display_time, parsed = parse_day_time(day_time, timezone_name, meridiem)
    if meridiem:
        display_time = f"{display_time} {meridiem.upper()}"
    tz_label = timezone_label(timezone_name, parsed)
    if parsed is None:
        return display_time, display_time, None, tz_label
    formatted = discord.utils.format_dt(parsed, "F")
    return display_time, formatted, parsed.isoformat(), tz_label


def summarize_match(match: Match) -> str:
    status_label = "Open"
    detail = "Awaiting result"
    if match.status == "closed":
        if match.outcome == "win":
            status_label = "Win"
        elif match.outcome == "loss":
            status_label = "Loss"
        else:
            status_label = "Closed"
        detail = f"Score: {match.score}" if match.score else "Score pending"

    return f"vs **{match.team}** at {match.display_time} — {status_label}{f' ({detail})' if detail else ''}"


def format_scheduled_time(match: Match) -> str:
    if not match.time_iso:
        return match.display_time
    try:
        dt = datetime.fromisoformat(match.time_iso)
        if dt.tzinfo is None and match.timezone:
            dt = dt.replace(tzinfo=ZoneInfo(match.timezone))
        return discord.utils.format_dt(dt, "F")
    except Exception:
        return match.display_time


async def resolve_text_channel(bot: commands.Bot, channel_id_env: Optional[str], fallback: Optional[discord.abc.Messageable]) -> Optional[discord.TextChannel]:
    if channel_id_env and channel_id_env.isdigit():
        channel = bot.get_channel(int(channel_id_env))
        if isinstance(channel, discord.TextChannel):
            return channel
        try:
            fetched = await bot.fetch_channel(int(channel_id_env))
        except Exception:
            fetched = None
        if isinstance(fetched, discord.TextChannel):
            return fetched
    return channel if isinstance(channel := fallback, discord.TextChannel) else None


class ScrimBot(commands.Bot):
    def __init__(self, store: StatsStore, guild_id: Optional[int] = None) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)
        self.store = store
        self.guild_id = guild_id
        self.view: Optional[StatsView] = None
        self.reminder_loop.add_exception_type(Exception)

    def get_stats_view(self) -> StatsView:
        if self.view is None:
            self.view = StatsView(self.store)
            self.add_view(self.view)
        return self.view

    async def setup_hook(self) -> None:
        self.view = StatsView(self.store)
        self.add_view(self.view)
        if not self.reminder_loop.is_running():
            self.reminder_loop.start()
        if self.guild_id:
            guild = discord.Object(id=self.guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    @tasks.loop(minutes=1)
    async def reminder_loop(self) -> None:
        for match in list(self.store.matches):
            if match.status != "open" or not match.time_iso or match.reminder_sent:
                continue
            try:
                event_time = datetime.fromisoformat(match.time_iso)
            except ValueError:
                continue
            tz_name = match.timezone or DEFAULT_TIMEZONE
            tz_info = None
            try:
                tz_info = ZoneInfo(tz_name)
            except Exception:
                tz_info = event_time.tzinfo

            if event_time.tzinfo is None and tz_info is not None:
                event_time = event_time.replace(tzinfo=tz_info)

            now = datetime.now(tz=event_time.tzinfo) if event_time.tzinfo else datetime.utcnow()
            if event_time - timedelta(minutes=10) <= now < event_time:
                if match.channel_id is None:
                    continue
                channel = self.get_channel(match.channel_id)
                if not isinstance(channel, discord.TextChannel):
                    continue
                mentions = " ".join(f"<@{user_id}>" for user_id in match.interested_user_ids)
                if not mentions:
                    mentions = "Reminder for the upcoming scrim."
                try:
                    await channel.send(
                        f"Reminder: scrim vs **{match.team}** at {match.display_time} in 10 minutes. {mentions}",
                        allowed_mentions=discord.AllowedMentions(users=True, roles=True, everyone=False),
                    )
                    self.store.mark_reminder_sent(match.match_id)
                except Exception:
                    continue


store = StatsStore(DATA_PATH)
guild_id_env = os.environ.get("GUILD_ID")
guild_id = int(guild_id_env) if guild_id_env and guild_id_env.isdigit() else None
scrim_role_id_env = os.environ.get("SCRIM_ROLE_ID")
bot = ScrimBot(store, guild_id=guild_id)


def member_has_command_role(member: Optional[discord.Member]) -> bool:
    if not COMMAND_ROLE_IDS:
        return True
    if member is None:
        return False
    return any(role.id in COMMAND_ROLE_IDS for role in member.roles)


async def ensure_command_role(interaction: discord.Interaction) -> bool:
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    if member_has_command_role(member):
        return True
    if member is None:
        raise app_commands.CheckFailure("This command can only be used in a server.")
    raise app_commands.CheckFailure("You do not have permission to use this bot.")


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    if isinstance(error, app_commands.CheckFailure):
        message = str(error) or "You do not have permission to use this bot."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
        return
    raise error


@bot.tree.command(name="scrim", description="Schedule a scrim with a team.")
@app_commands.check(ensure_command_role)
@app_commands.describe(
    team_name="Opponent team name",
    time="Time in DD HH:MM format",
    meridiem="AM or PM to clarify the time",
)
@app_commands.choices(
    timezone=[
        app_commands.Choice(name="Eastern (EST)", value="EST"),
        app_commands.Choice(name="Central (CST)", value="CST"),
        app_commands.Choice(name="Pacific (PST)", value="PST"),
    ],
    meridiem=[
        app_commands.Choice(name="AM", value="AM"),
        app_commands.Choice(name="PM", value="PM"),
    ],
)
async def scrim_command(
    interaction: discord.Interaction,
    team_name: str,
    time: str,
    timezone: Optional[app_commands.Choice[str]] = None,
    meridiem: Optional[app_commands.Choice[str]] = None,
) -> None:
    target_channel = await resolve_text_channel(bot, SCRIM_CHANNEL_ID_ENV, interaction.channel)
    if target_channel is None:
        await interaction.response.send_message(
            "Could not find a text channel to post the scrim. Check SCRIM_CHANNEL_ID.", ephemeral=True
        )
        return

    timezone_name = resolve_timezone_name(timezone.value if timezone else None)
    display_time, timestamp, iso_time, tz_label = create_timestamp(
        time, timezone_name, meridiem.value if meridiem else None
    )
    annotated_time = f"{display_time} {tz_label}".strip()
    match = store.add_match(team_name, annotated_time, iso_time, timezone_name)
    role_mention = f"<@&{scrim_role_id_env}> " if scrim_role_id_env else ""
    embed = discord.Embed(
        title="Scrim scheduled",
        description=f"Team: **{team_name}**\nTime: {timestamp} ({tz_label})",
        color=discord.Color.blurple(),
    )
    embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
    scrim_message = await target_channel.send(
        content=f"{role_mention}Scrim at {annotated_time} against **{team_name}**",
        embed=embed,
        allowed_mentions=discord.AllowedMentions(roles=True),
    )

    store.link_message(match.match_id, scrim_message.channel.id, scrim_message.id)

    for emoji in ("✅", "❌"):
        try:
            await scrim_message.add_reaction(emoji)
        except Exception:
            pass

    try:
        thread = await scrim_message.create_thread(name=f"Scrim vs {team_name}")
        store.link_thread(match.match_id, thread.id)
    except Exception:
        # Fail silently if threads are not allowed or cannot be created.
        pass

    await interaction.response.send_message(
        f"Scrim posted in {target_channel.mention}.", ephemeral=True
    )


@bot.tree.command(name="check-scrims", description="Show all scrims and their results.")
@app_commands.check(ensure_command_role)
async def check_scrims(interaction: discord.Interaction) -> None:
    lines = [summarize_match(match) for match in store.matches]
    description = "\n".join(lines) if lines else "No scrims recorded yet."
    embed = discord.Embed(title="Scrim history", description=description, color=discord.Color.blurple())
    view = ScrimManagerView(store, interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


@bot.tree.command(name="submit-scores", description="Record a match result.")
@app_commands.check(ensure_command_role)
@app_commands.describe(
    match_id="Choose an open match to record",
    outcome="Did we win or lose?",
    overall_score="Score summary, e.g. 13-11",
)
@app_commands.choices(outcome=[
    app_commands.Choice(name="Win", value="win"),
    app_commands.Choice(name="Loss", value="loss"),
])
async def submit_scores(
    interaction: discord.Interaction,
    match_id: str,
    outcome: app_commands.Choice[str],
    overall_score: str,
) -> None:
    if not match_id.isdigit():
        await interaction.response.send_message("Please choose a valid match.", ephemeral=True)
        return

    match = store.close_match(int(match_id), outcome.value, overall_score)  # type: ignore[arg-type]
    if match is None:
        await interaction.response.send_message("Match not found or already closed.", ephemeral=True)
        return

    wins, losses = store.totals()

    won = outcome.value == "win"
    result_label = "Win" if won else "Loss"
    embed_color = discord.Color.brand_green() if won else discord.Color.red()
    scheduled_value = format_scheduled_time(match)

    embed = discord.Embed(
        title="Match recorded",
        description=(
            f"{result_label} vs **{match.team}**\n"
            f"Score: **{overall_score}**\n"
            f"Scheduled: {scheduled_value}"
        ),
        color=embed_color,
    )
    embed.add_field(name="Record", value=f"Wins: {wins}\nLosses: {losses}", inline=False)
    embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)

    view = bot.get_stats_view()
    results_channel = await resolve_text_channel(bot, RESULTS_CHANNEL_ID_ENV, interaction.channel)
    if results_channel:
        await results_channel.send(embed=embed, view=view)
        await interaction.response.send_message(
            f"Result posted to {results_channel.mention}.", embed=embed, view=view, ephemeral=True
        )
    else:
        await interaction.response.send_message(embed=embed, view=view)


@submit_scores.autocomplete("match_id")
async def match_autocomplete(interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
    choices: List[app_commands.Choice[str]] = []
    for match in store.list_open_matches():
        label = f"vs {match.team} at {match.display_time}"
        choices.append(app_commands.Choice(name=label[:100], value=str(match.match_id)))
    return choices[:25]


@bot.tree.command(name="cancel-match", description="Cancel an open match.")
@app_commands.check(ensure_command_role)
@app_commands.describe(match_id="Choose an open match to cancel")
async def cancel_match(interaction: discord.Interaction, match_id: str) -> None:
    if not match_id.isdigit():
        await interaction.response.send_message("Please choose a valid match.", ephemeral=True)
        return

    removed = store.cancel_match(int(match_id))
    if removed is None:
        await interaction.response.send_message("Match not found or already closed.", ephemeral=True)
        return

    if removed.thread_id:
        thread_channel = bot.get_channel(removed.thread_id)
        if not isinstance(thread_channel, (discord.Thread, discord.TextChannel)):
            try:
                fetched = await bot.fetch_channel(removed.thread_id)
            except Exception:
                fetched = None
            thread_channel = fetched if isinstance(fetched, (discord.Thread, discord.TextChannel)) else None
        if isinstance(thread_channel, (discord.Thread, discord.TextChannel)):
            try:
                await thread_channel.send("This scrim has been cancelled.")
            except Exception:
                pass

    await interaction.response.send_message(
        f"Cancelled match vs {removed.team} at {removed.display_time}.", ephemeral=True
    )


@cancel_match.autocomplete("match_id")
async def cancel_autocomplete(interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
    return await match_autocomplete(interaction, current)


@bot.tree.command(name="edit-time", description="Edit the time of a scheduled scrim.")
@app_commands.check(ensure_command_role)
@app_commands.describe(
    match_id="Choose an open match to edit",
    time="New time in DD HH:MM format",
    meridiem="AM or PM to clarify the time",
)
@app_commands.choices(
    timezone=[
        app_commands.Choice(name="Eastern (EST)", value="EST"),
        app_commands.Choice(name="Central (CST)", value="CST"),
        app_commands.Choice(name="Pacific (PST)", value="PST"),
    ],
    meridiem=[
        app_commands.Choice(name="AM", value="AM"),
        app_commands.Choice(name="PM", value="PM"),
    ],
)
async def edit_time(
    interaction: discord.Interaction,
    match_id: str,
    time: str,
    timezone: Optional[app_commands.Choice[str]] = None,
    meridiem: Optional[app_commands.Choice[str]] = None,
) -> None:
    if not match_id.isdigit():
        await interaction.response.send_message("Please choose a valid match.", ephemeral=True)
        return

    match = store.get_match(int(match_id))
    if match is None or match.status != "open":
        await interaction.response.send_message("Match not found or already closed.", ephemeral=True)
        return

    timezone_name = resolve_timezone_name(timezone.value if timezone else match.timezone)
    display_time, timestamp, iso_time, tz_label = create_timestamp(
        time, timezone_name, meridiem.value if meridiem else None
    )
    annotated_time = f"{display_time} {tz_label}".strip()
    updated = store.update_match_time(match.match_id, annotated_time, iso_time, timezone_name)
    if updated is None:
        await interaction.response.send_message("Match not found or already closed.", ephemeral=True)
        return

    if updated.thread_id:
        thread_channel = bot.get_channel(updated.thread_id)
        if not isinstance(thread_channel, (discord.Thread, discord.TextChannel)):
            try:
                fetched = await bot.fetch_channel(updated.thread_id)
            except Exception:
                fetched = None
            thread_channel = fetched if isinstance(fetched, (discord.Thread, discord.TextChannel)) else None
        if isinstance(thread_channel, (discord.Thread, discord.TextChannel)):
            try:
                await thread_channel.send(f"This scrim time has been changed to {timestamp} ({tz_label}).")
            except Exception:
                pass

    await interaction.response.send_message(
        f"Updated scrim time for vs {updated.team} to {annotated_time}.", ephemeral=True
    )


@edit_time.autocomplete("match_id")
async def edit_time_autocomplete(
    interaction: discord.Interaction, current: str
) -> List[app_commands.Choice[str]]:
    return await match_autocomplete(interaction, current)


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent) -> None:
    if bot.user and payload.user_id == bot.user.id:
        return

    emoji = str(payload.emoji)
    if emoji != "✅":
        return

    for match in store.list_open_matches():
        if match.message_id == payload.message_id:
            store.add_participant(match.match_id, payload.user_id)
            break


def main() -> None:
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise SystemExit(
            "DISCORD_TOKEN environment variable is required. Set it in a .env file or export it before running the bot."
        )
    bot.run(token)


if __name__ == "__main__":
    main()
