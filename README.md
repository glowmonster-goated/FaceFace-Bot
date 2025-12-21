# FaceFace Bot

A lightweight Discord bot that schedules scrims and tracks match results with slash commands.

## Features

- `/scrim [team_name] [time] [timezone]` – schedules a scrim and echoes a Discord-formatted timestamp when possible.
- `/submit-scores [team_name] [outcome] [overall_score]` – records a win/loss, keeps running totals, and exposes buttons to list all wins or losses (case-insensitive team names).
- Persistent stats saved to `stats.json` so you keep history across restarts.

## Prerequisites

- Python 3.10+
- A Discord bot token with the applications.commands scope enabled

## Setup

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Set environment variables:
   ```bash
   export DISCORD_TOKEN="your_bot_token"
   # Optional: limit slash command registration to a single guild for faster sync
   export GUILD_ID="123456789012345678"
   ```
3. Run the bot:
   ```bash
   python bot.py
   ```

## Commands

### `/scrim`

- **team_name**: Opponent team name.
- **time**: In `YYYY-MM-DD HH:MM` format. If it parses, the bot responds with a Discord timestamp; otherwise it echoes the raw text.
- **timezone** (optional): IANA timezone, e.g., `UTC` or `America/New_York` (defaults to `UTC`).

### `/submit-scores`

- **team_name**: Opponent team name (case-insensitive for tracking).
- **outcome**: `win` or `loss`.
- **overall_score**: Free-form score string (e.g., `13-11`).

The response includes running totals plus buttons to show all wins or all losses. When listing opponents, repeat matches display a count (e.g., `Team ABC (2)`).

## Data

Match history is stored in `stats.json` in the project root. The file is ignored by git and will be created automatically.

## Notes

- Commands sync globally by default. Provide `GUILD_ID` during local testing to speed up registration.
- Time parsing relies on ISO-style inputs; if parsing fails, the bot keeps your original time text and timezone label.
