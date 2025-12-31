import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
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
SCRIM_ROLE_ID_ENV = os.environ.get("SCRIM_ROLE_ID")

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

# -------------------- models --------------------

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
    display_time: str  # user input display
    time_iso: Optional[str]  # ISO datetime with tz
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

# -------------------- store --------------------

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
            content = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return
        if not isinstance(content, dict):
            return

        teams_data = content.get("teams", {})
        if isinstance(teams_data, dict):
            for key, entry in teams_data.items():
                if isinstance(entry, dict):
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
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

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

# -------------------- views --------------------

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

# -------------------- helpers --------------------

def resolve_timezone_name(timezone_key: Optional[str]) -> str:
    if timezone_key and timezone_key in NA_TIMEZONES:
        return NA_TIMEZONES[timezone_key]
    # validate DEFAULT_TIMEZONE
    try:
        ZoneInfo(DEFAULT_TIMEZONE)
        return DEFAULT_TIMEZONE
    except Exception:
        return "UTC"

def parse_time_today_or_tomorrow(time_str: str, tz_name: str) -> Optional[datetime]:
    """
    Accepts: "4 PM", "4:30 PM", "16:30", "16"
    Schedules today; if already passed, schedules tomorrow.
    """
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = timezone.utc

    now = datetime.now(tz)

    s = time_str.strip().lower().replace(".", "")
    fmts = ["%I %p", "%I:%M %p", "%H:%M", "%H"]
    parsed_time = None
    for fmt in fmts:
        try:
            parsed_time = datetime.strptime(s, fmt).time()
            break
        except ValueError:
            pass

    if parsed_time is None:
        return None

    dt = datetime(now.year, now.month, now.day, parsed_time.hour, parsed_time.minute, tzinfo=tz)
    if dt <= now:
        dt = dt + timedelta(days=1)
    return dt

def create_timestamp(time_str: str, tz_name: str) -> Tuple[str, str, Optional[str], str]:
    dt = parse_time_today_or_tomorrow(time_str, tz_name)
    if dt is None:
        # fall back to raw display
        return time_str, time_str, None, tz_name

    # discord timestamp string (viewer-local)
    formatted = discord.utils.format_dt(dt, "F")

    # nice tz label
    tz_label = dt.tzname() or tz_name

    return time_str, formatted, dt.isoformat(), tz_label

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
        return discord.utils.format_dt(dt, "F")
    except Exception:
        return match.display_time

async def resolve_text_channel(
    bot: commands.Bot,
    channel_id_env: Optional[str],
    fallback: Optional[discord.abc.Messageable]
) -> Optional[discord.TextChannel]:
    if channel_id_env and channel_id_env.isdigit():
        cid = int(channel_id_env)
        ch = bot.get_channel(cid)
        if isinstance(ch, discord.TextChannel):
            return ch
        try:
            fetched = await bot.fetch_channel(cid)
        except Exception:
            fetched = None
        if isinstance(fetched, discord.TextChannel):
            return fetched

    return fallback if isinstance(fallback, discord.TextChannel) else None

# -------------------- bot --------------------

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

    @tasks.loop(seconds=20)
    async def reminder_loop(self) -> None:
        # checks every 20 seconds; good enough for 10-min warnings
        for match in list(self.store.matches):
            if match.status != "open" or not match.time_iso or match.reminder_sent:
                continue

            try:
                event_time = datetime.fromisoformat(match.time_iso)
            except ValueError:
                continue

            if event_time.tzinfo is None:
                # should not happen, but keep safe
                event_time = event_time.replace(tzinfo=timezone.utc)

            now = datetime.now(tz=event_time.tzinfo)

            # 10 minute window
            if event_time - timedelta(minutes=10) <= now < event_time:
                if match.channel_id is None:
                    continue

                # get channel (cache) or fetch (API)
                channel = self.get_channel(match.channel_id)
                if channel is None:
                    try:
                        channel = await self.fetch_channel(match.channel_id)
                    except Exception:
                        channel = None

                if not isinstance(channel, discord.TextChannel):
                    continue

                role_ping = ""
                if SCRIM_ROLE_ID_ENV and SCRIM_ROLE_ID_ENV.isdigit():
                    role_ping = f"<@&{SCRIM_ROLE_ID_ENV}> "

                user_mentions = " ".join(f"<@{uid}>" for uid in match.interested_user_ids)

                msg = (
                    f"⏰ **10 minute warning** — scrim vs **{match.team}** starts {discord.utils.format_dt(event_time, 'R')} "
                    f"({discord.utils.format_dt(event_time, 't')})\n"
                    f"{role_ping}{user_mentions}".strip()
                )

                try:
                    await channel.send(
                        msg,
                        allowed_mentions=discord.AllowedMentions(users=True, roles=True, everyone=False),
                    )
                    self.store.mark_reminder_sent(match.match_id)
                except Exception:
                    continue

# -------------------- auth / checks --------------------

store = StatsStore(DATA_PATH)
guild_id_env = os.environ.get("GUILD_ID")
guild_id = int(guild_id_env) if guild_id_env and guild_id_env.isdigit() else None
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

# -------------------- commands --------------------

@bot.tree.command(name="scrim", description="Schedule a scrim with a team.")
@app_commands.check(ensure_command_role)
@app_commands.describe(
    team_name="Opponent team name",
    time="Local time like '4 PM', '4:30 PM', or '16:30' (today; tomorrow if already passed).",
)
@app_commands.choices(
    timezone=[
        app_commands.Choice(name="Eastern (America/New_York)", value="EST"),
        app_commands.Choice(name="Central (America/Chicago)", value="CST"),
        app_commands.Choice(name="Pacific (America/Los_Angeles)", value="PST"),
    ],
)
async def scrim_command(
    interaction: discord.Interaction,
    team_name: str,
    time: str,
    timezone: Optional[app_commands.Choice[str]] = None,
) -> None:
    target_channel = await resolve_text_channel(bot, SCRIM_CHANNEL_ID_ENV, interaction.channel)
    if target_channel is None:
        await interaction.response.send_message(
            "Could not find a text channel to post the scrim. Check SCRIM_CHANNEL_ID.", ephemeral=True
        )
        return

    tz_name = resolve_timezone_name(timezone.value if timezone else None)

    display_time, timestamp_str, iso_time, tz_label = create_timestamp(time, tz_name)
    if iso_time is None:
        await interaction.response.send_message(
            "Could not parse that time. Try: `4 PM`, `4:30 PM`, or `16:30`.",
            ephemeral=True
        )
        return

    annotated_time = f"{display_time} {tz_label}".strip()
    match = store.add_match(team_name, annotated_time, iso_time, tz_name)

    role_mention = f"<@&{SCRIM_ROLE_ID_ENV}> " if SCRIM_ROLE_ID_ENV and SCRIM_ROLE_ID_ENV.isdigit() else ""

    embed = discord.Embed(
        title="Scrim scheduled",
        description=f"Team: **{team_name}**\nTime: {timestamp_str} ({tz_label})",
        color=discord.Color.blurple(),
    )
    embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)

    scrim_message = await target_channel.send(
        content=f"{role_mention}Scrim scheduled vs **{team_name}** — {timestamp_str}",
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

# -------------------- run --------------------

def main() -> None:
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise SystemExit("DISCORD_TOKEN env var is required.")
    bot.run(token)

if __name__ == "__main__":
    main()
