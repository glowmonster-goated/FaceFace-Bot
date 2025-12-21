# FaceFace Bot

A lightweight Discord bot that schedules scrims and tracks match results with slash commands.

## Features

- `/scrim [team_name] [time]` – schedules a scrim, pings a configured role, and creates a discussion thread.
- `/submit-scores [match] [outcome] [overall_score]` – records a win/loss for an open scrim, keeps running totals, and exposes buttons to list all wins or losses (case-insensitive team names).
- Persistent stats saved to `stats.json` so you keep history across restarts.

## Prerequisites

- Python 3.10+
- A Discord bot token with the applications.commands scope enabled

## Setup

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Copy `.env.example` to `.env` and fill in the values, or export the variables manually:
   ```bash
   export DISCORD_TOKEN="your_bot_token"
   # Optional: limit slash command registration to a single guild for faster sync
   export GUILD_ID="123456789012345678"
   # Optional: role to ping for new scrims
   export SCRIM_ROLE_ID="987654321098765432"
   # Optional: text channel where scrim announcements and threads should be created
   export SCRIM_CHANNEL_ID="123456789012345678"
   # Optional: text channel where match results will be posted
   export RESULTS_CHANNEL_ID="234567890123456789"
   # Optional: timezone for parsing DD HH:MM inputs (defaults to UTC)
   export TIMEZONE="UTC"
   ```
3. Run the bot:
   ```bash
   python bot.py
   ```

## Commands

### `/scrim`

- **team_name**: Opponent team name.
- **time**: In `DD HH:MM` format. Times are parsed in the configured `TIMEZONE` (defaults to `UTC`) and rendered as Discord timestamps; if parsing fails, the raw text is echoed.
- Behavior: posts to `SCRIM_CHANNEL_ID` (if set, otherwise the command channel), pings the `SCRIM_ROLE_ID` (if set), labels the message with a match number, and opens a thread for discussion.

### `/submit-scores`

- **match**: Choose from open scrims (autocomplete shows `#id vs team at time`).
- **outcome**: `win` or `loss`.
- **overall_score**: Free-form score string (e.g., `13-11`).

The response includes running totals plus buttons to show all wins or all losses. When listing opponents, repeat matches display a count (e.g., `Team ABC (2)`). Closed scrims drop out of the autocomplete list. Results are posted to `RESULTS_CHANNEL_ID` when set.

## Data

Match history is stored in `stats.json` in the project root. The file is ignored by git and will be created automatically.

## Notes

- Commands sync globally by default. Provide `GUILD_ID` during local testing to speed up registration.
- Time parsing expects `DD HH:MM` and uses `TIMEZONE`; if parsing fails, the bot keeps your original time text.
