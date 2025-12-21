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


class StatsStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.teams: Dict[str, TeamRecord] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            content = json.loads(self.path.read_text())
        except json.JSONDecodeError:
            return

        teams_data = content.get("teams", {}) if isinstance(content, dict) else {}
        for key, entry in teams_data.items():
            self.teams[key] = TeamRecord.from_dict(entry)

    def _save(self) -> None:
        payload = {"teams": {k: v.as_dict() for k, v in self.teams.items()}}
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


def create_timestamp(time_text: str, timezone: str) -> str:
    tz: Optional[ZoneInfo]
    try:
        tz = ZoneInfo(timezone)
    except Exception:
        tz = None

    if tz is None:
        return f"{time_text} ({timezone})"

    try:
        parsed = datetime.fromisoformat(time_text)
    except ValueError:
        return f"{time_text} ({timezone})"

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    else:
        parsed = parsed.astimezone(tz)

    return discord.utils.format_dt(parsed, "F")


class ScrimBot(commands.Bot):
    def __init__(self, store: StatsStore, guild_id: Optional[int] = None) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)
        self.store = store
        self.guild_id = guild_id
        self.view = StatsView(store)

    async def setup_hook(self) -> None:
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
bot = ScrimBot(store, guild_id=guild_id)


@bot.tree.command(name="scrim", description="Schedule a scrim with a team.")
@app_commands.describe(team_name="Opponent team name", time="Time in YYYY-MM-DD HH:MM format", timezone="IANA timezone, e.g. UTC or America/New_York")
async def scrim_command(
    interaction: discord.Interaction,
    team_name: str,
    time: str,
    timezone: str = "UTC",
) -> None:
    timestamp = create_timestamp(time, timezone)
    embed = discord.Embed(
        title="Scrim scheduled",
        description=f"Team: **{team_name}**\nTime: {timestamp}",
        color=discord.Color.blurple(),
    )
    embed.set_footer(text="Times use ISO format: YYYY-MM-DD HH:MM")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="submit-scores", description="Record a match result.")
@app_commands.describe(
    team_name="Opponent team name",
    outcome="Did we win or lose?",
    overall_score="Score summary, e.g. 13-11",
)
@app_commands.choices(outcome=[
    app_commands.Choice(name="Win", value="win"),
    app_commands.Choice(name="Loss", value="loss"),
])
async def submit_scores(
    interaction: discord.Interaction,
    team_name: str,
    outcome: app_commands.Choice[str],
    overall_score: str,
) -> None:
    store.record_result(team_name, outcome.value)  # type: ignore[arg-type]
    wins, losses = store.totals()

    embed = discord.Embed(title="Match recorded", color=discord.Color.brand_green())
    embed.add_field(name="Result", value=outcome.name, inline=True)
    embed.add_field(name="Opponent", value=team_name, inline=True)
    embed.add_field(name="Score", value=overall_score, inline=True)
    embed.add_field(name="Totals", value=f"Wins: {wins}\nLosses: {losses}", inline=False)

    await interaction.response.send_message(embed=embed, view=bot.view)


def main() -> None:
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN environment variable is required")
    bot.run(token)


if __name__ == "__main__":
    main()
