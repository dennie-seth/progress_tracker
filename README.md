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

## Deploy on a VDS (production, default)

The root `docker-compose.yml` is the **production** stack: bot-app +
self-hosted `telegram-bot-api` + Postgres on a single host, sharing the
bot-api data directory so the bot reads uploaded videos directly off disk.

```bash
# 1. Create the shared data directory with the right ownership BEFORE the
#    first `up`. Both containers run as UID 1000; pre-chowning saves you
#    from a confusing "permission denied" on the bot-api's first write.
mkdir -p telegram-bot-api-data
sudo chown -R 1000:1000 telegram-bot-api-data

# 2. Create .env once on the VDS (gitignored — `git pull` won't touch it):
cp .env.example .env            # set BOT_TOKEN and POSTGRES_PASSWORD;
                                # the latter is required (compose refuses to
                                # start without it).

# 3. Build everything and bring up the stack. The first build compiles
#    tdlib + telegram-bot-api from source (~10-15 min); rebuilds reuse cache.
#
# Note on TELEGRAM_API_ID / TELEGRAM_API_HASH: telegram-bot-api needs them
# at first launch to register with Telegram. After that they're cached in
# `telegram-bot-api-data/` and not required on subsequent restarts. If
# you're deploying fresh, export them in the shell that runs compose
# (compose interpolates `${TELEGRAM_API_ID}` / `${TELEGRAM_API_HASH}` into
# the bot-api container) — get them from https://my.telegram.org/apps.
docker compose up -d --build
```

That sets `BOT_API_LOCAL_FILES=true` so the bot uses `LocalFileFetcher`
(direct disk read, no `DeleteFile` cleanup) and bind-mounts
`./telegram-bot-api-data` read-only into the bot container. SOCKS and
HTTP Basic Auth aren't needed when both services run on the same host.

The "git clone → cd → docker compose up" shape is intentional so a dumb
cron job can do `git pull && docker compose up -d --build` on the VDS.
`.env` lives outside git, so cron pulls never overwrite secrets.

### Disk-based recovery (no DB required)

Even without a Postgres backup, the bot survives a host swap as long as
the `media_data` volume comes along:

- Every successful upload and delete dumps a per-user JSON manifest to
  `<MEDIA_DIR>/<user_id>/manifest.json` (atomic write — SIGKILL leaves
  the previous valid file intact). A second sweep runs on graceful
  shutdown.
- Stored video filenames encode their tags
  (`<user>/<tag1>.<tag2>.<uuid>.mp4`) so even a missing/corrupt manifest
  still recovers tags + video↔tag links from the on-disk filenames.
- At startup, if the `videos` table is empty, the bot reads manifests
  and rebuilds the DB inside one transaction. Recovery is one-shot —
  once any video row exists, it's a no-op.

Filename-only recovery degrades a few fields gracefully: `created_at`
falls back to file mtime, `caption` is lost, and `telegram_file_id` is
empty (the `/delete` listing then re-uploads the local copy via
`FSInputFile` instead of the cached file_id). Re-uploading any clip
restores its rich metadata from the next manifest dump.

### Backups

The Postgres data lives in the named volume `db_data` and survives
`docker compose down` but **not** `docker compose down -v` or a host wipe.
Periodic dump:

```bash
docker compose exec db pg_dump -U postgres progress_tracker \
    > "backups/$(date +%F).sql"
```

Restore:

```bash
docker compose exec -T db psql -U postgres -d progress_tracker \
    < backups/<date>.sql
```

Compiled videos in the `media_data` volume and uploaded source clips in
`telegram-bot-api-data/` are intentionally not part of the DB backup —
the source clips are recoverable from the chats that produced them, and
the manifest-based recovery above handles the metadata.

## Local dev (cloud Telegram, no bot-api server)

```bash
cp .env.example .env            # set BOT_TOKEN from @BotFather
docker compose -f docker-compose.dev.yml up --build
```

Then DM your bot `/start`. This stack is bot + Postgres only and uses the
HTTPS download path through `RemoteFileFetcher`.

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
