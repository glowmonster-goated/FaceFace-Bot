import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands


DATA_PATH = Path("stats.json")
DEFAULT_TIMEZONE = os.environ.get("TIMEZONE", "UTC")
SCRIM_CHANNEL_ID_ENV = os.environ.get("SCRIM_CHANNEL_ID")
RESULTS_CHANNEL_ID_ENV = os.environ.get("RESULTS_CHANNEL_ID")


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
    status: Literal["open", "closed"] = "open"
    outcome: Optional[Literal["win", "loss"]] = None
    score: Optional[str] = None

    def as_dict(self) -> Dict[str, object]:
        return {
            "match_id": self.match_id,
            "team": self.team,
            "display_time": self.display_time,
            "time_iso": self.time_iso,
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

    def add_match(self, team: str, display_time: str, time_iso: Optional[str]) -> Match:
        match = Match(
            match_id=self.next_match_id,
            team=team.strip(),
            display_time=display_time,
            time_iso=time_iso,
        )
        self.matches.append(match)
        self.next_match_id += 1
        self._save()
        return match

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


def parse_day_time(day_time: str) -> Tuple[str, Optional[datetime]]:
    """Parse `DD HH:MM` into a datetime in the configured timezone."""

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

    try:
        tz = ZoneInfo(DEFAULT_TIMEZONE)
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


def create_timestamp(day_time: str) -> Tuple[str, str, Optional[str]]:
    display_time, parsed = parse_day_time(day_time)
    if parsed is None:
        return display_time, display_time, None
    formatted = discord.utils.format_dt(parsed, "F")
    return display_time, formatted, parsed.isoformat()


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

    def get_stats_view(self) -> StatsView:
        if self.view is None:
            self.view = StatsView(self.store)
            self.add_view(self.view)
        return self.view

    async def setup_hook(self) -> None:
        self.view = StatsView(self.store)
        self.add_view(self.view)
        if self.guild_id:
            guild = discord.Object(id=self.guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()


store = StatsStore(DATA_PATH)
guild_id_env = os.environ.get("GUILD_ID")
guild_id = int(guild_id_env) if guild_id_env and guild_id_env.isdigit() else None
scrim_role_id_env = os.environ.get("SCRIM_ROLE_ID")
bot = ScrimBot(store, guild_id=guild_id)


@bot.tree.command(name="scrim", description="Schedule a scrim with a team.")
@app_commands.describe(team_name="Opponent team name", time="Time in DD HH:MM format")
async def scrim_command(
    interaction: discord.Interaction,
    team_name: str,
    time: str,
) -> None:
    target_channel = await resolve_text_channel(bot, SCRIM_CHANNEL_ID_ENV, interaction.channel)
    if target_channel is None:
        await interaction.response.send_message(
            "Could not find a text channel to post the scrim. Check SCRIM_CHANNEL_ID.", ephemeral=True
        )
        return

    display_time, timestamp, iso_time = create_timestamp(time)
    match = store.add_match(team_name, display_time, iso_time)
    role_mention = f"<@&{scrim_role_id_env}> " if scrim_role_id_env else ""
    embed = discord.Embed(
        title="Scrim scheduled",
        description=f"Team: **{team_name}**\nTime: {timestamp}",
        color=discord.Color.blurple(),
    )
    embed.set_footer(text="Format: DD HH:MM (uses TIMEZONE env if set)")
    scrim_message = await target_channel.send(
        content=f"{role_mention}Scrim at {display_time} against **{team_name}** (Match #{match.match_id})",
        embed=embed,
        allowed_mentions=discord.AllowedMentions(roles=True),
    )

    try:
        await scrim_message.create_thread(name=f"Scrim vs {team_name}")
    except Exception:
        # Fail silently if threads are not allowed or cannot be created.
        pass

    await interaction.response.send_message(
        f"Scrim posted in {target_channel.mention} as Match #{match.match_id}.", ephemeral=True
    )


@bot.tree.command(name="submit-scores", description="Record a match result.")
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

    embed = discord.Embed(title="Match recorded", color=discord.Color.brand_green())
    embed.add_field(name="Result", value=outcome.name, inline=True)
    embed.add_field(name="Opponent", value=match.team, inline=True)
    embed.add_field(name="Score", value=overall_score, inline=True)
    embed.add_field(name="Scheduled time", value=match.display_time, inline=True)
    embed.add_field(name="Totals", value=f"Wins: {wins}\nLosses: {losses}", inline=False)

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
        label = f"#{match.match_id} vs {match.team} at {match.display_time}"
        choices.append(app_commands.Choice(name=label, value=str(match.match_id)))
    return choices[:25]


def main() -> None:
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN environment variable is required")
    bot.run(token)


if __name__ == "__main__":
    main()
