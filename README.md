# progress-tracker

A self-hostable Telegram bot that turns your training clips into a progress reel.

Upload short videos tagged with hashtags (`#squat`, `#bachata-basic`, ...). The bot
stores them per user and, on demand, compiles a ≤30s video showing progress from
the oldest matching clip to the newest.

**Status:** alpha — milestone 1 (skeleton) in place.

## License

MIT — see [LICENSE](LICENSE).

## Stack

- Python 3.11+, asyncio
- [aiogram 3](https://docs.aiogram.dev/) for the Telegram layer
- PostgreSQL 16 via SQLAlchemy 2 async + asyncpg
- Alembic for migrations
- ffmpeg (system binary) for video compilation
- Docker Compose for one-command self-host

## Quick start (Docker)

```bash
cp .env.example .env            # set BOT_TOKEN from @BotFather
docker compose up --build
```

Then DM your bot `/start`.

## Deploy on a VDS (production)

For a production deployment that co-locates the bot with a self-hosted
`telegram-bot-api` server (so the bot reads uploaded videos directly off
shared disk instead of downloading them over HTTP), use the dedicated
compose file:

```bash
# Pre-build the bot-api image once from the upstream source:
docker build -t telegram-bot-api-local /path/to/telegram-bot-api

# Bring up bot-app + telegram-bot-api + postgres on the VDS:
cp .env.example .env            # set BOT_TOKEN, TELEGRAM_API_ID, TELEGRAM_API_HASH
docker compose -f docker-compose.vds.yml up -d --build
```

That compose file sets `BOT_API_LOCAL_FILES=true` so the bot uses
`LocalFileFetcher` (direct disk read, no `DeleteFile` cleanup) and bind-
mounts `./telegram-bot-api-data` read-only into the bot container. SOCKS
and HTTP Basic Auth aren't needed when both services run on the same host.

## Quick start (local, without Docker)

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env            # set BOT_TOKEN and DATABASE_URL

# Make sure Postgres is running and ffmpeg is on PATH
python -m progress_tracker
```

## How it works

1. Send a video to the bot with a caption containing one or more hashtags:
   `#squat felt strong today`
2. Bot saves the file, parses tags, and stores metadata.
3. If prior videos with the same tags exist, the bot offers to generate a
   progress reel and asks:
   - **Tag** to track (skipped when unambiguous)
   - **Date range** (all time / 6 months / 1 month / custom)
   - **Target duration** (10s / 15s / 30s / custom)
   - **Overlay the upload date** on each clip? (yes / no)
4. The bot runs ffmpeg, concatenates clips oldest → newest, and sends the result
   back.

Clips that fit into the per-clip time budget are kept at normal speed; longer
clips are sped up (`setpts` + `atempo`) to fit.

## Project layout

See [planning notes](#) and the top-level directory — the code lives in
[src/progress_tracker/](src/progress_tracker/).

## Contributing

Issues and PRs welcome. Run `ruff check .`, `mypy src/`, and `pytest` before
sending a PR.
