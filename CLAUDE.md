# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Rules

Project rules live under [.rules/](.rules/), one file per area. **Read the
file matching the directory you're touching before making changes.**

- [.rules/general.md](.rules/general.md) — TDD, secrets, graceful shutdown
- [.rules/db.md](.rules/db.md) — schema and tag scoping
- [.rules/bot_api.md](.rules/bot_api.md) — endpoint routing, SOCKS, the two FileFetcher modes
- [.rules/storage.md](.rules/storage.md) — Storage Protocol contract
- [.rules/handlers.md](.rules/handlers.md) — router registration, FSM storage
- [.rules/services.md](.rules/services.md) — ingest path, long-job handling
- [.rules/video.md](.rules/video.md) — ffmpeg subprocess, clip trimming + selection

## Project status

Early-alpha. Milestones 1, 2, 2.5, 3, 5 and 6 are implemented. Roadmap at
`C:\Users\denni\.claude\plans\enumerated-twirling-glacier.md`; the appendix
of that file holds the most recent refactor plan (VDS co-location).

What works right now:
- `/start` and `/help` reply with a welcome message.
- Postgres with the full schema (users / tags / videos / video_tags /
  compilations) — Alembic runs `upgrade head` on every bot startup.
- Optional routing through a remote `telegram-bot-api` server via SOCKS5
  (controlled by `BOT_API_URL` + `SOCKS_PROXY_URL`).
- Video upload pipeline: user sends a video with hashtags in the caption,
  bot ingests it via the configured `FileFetcher` (HTTP download for
  dev-from-home, direct disk read for the co-located VDS deploy), saves
  into `LocalStorage`, persists `User`/`Tag`/`Video`/`VideoTag` rows, and
  replies with a saved-confirmation that includes how many prior clips
  share the same tags.
- Compile FSM + ffmpeg compiler: `/compile` walks the user through tag /
  range / duration / overlay choices and produces an iOS-Photos-compatible
  `.mov` reel.

Not yet built: history/library commands (milestone 4 partials), tests/CI
workflow file (milestone 8). ffprobe isn't wired on ingest — for now we
trust Telegram's `Video` duration/width/height fields. Add a probe step in
milestone 4 if we need fps or accurate duration.

## Commands

All commands assume `cwd = F:\PetProjects\progress_tracker`.

**Run the full stack (recommended):**
```powershell
docker compose up --build       # builds bot image, starts Postgres + bot
docker compose down             # stop
docker compose logs -f bot      # tail bot logs
```

**Run the bot natively (milestone 1 only — no DB needed yet):**
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python -m progress_tracker      # reads .env, starts polling
```

**Lint / type-check / test — inside the bot container** (image includes `[dev]` extras):
```powershell
# Bind-mount src/ and tests/ so edits on host reflect without a rebuild.
# The bot image uses an editable install, so the mounted src/ becomes the
# live package.
docker compose run --rm `
    -v "${PWD}/src:/app/src" `
    -v "${PWD}/tests:/app/tests" `
    bot pytest

docker compose run --rm -v "${PWD}/src:/app/src" -v "${PWD}/tests:/app/tests" bot pytest tests/test_config.py
docker compose run --rm -v "${PWD}/src:/app/src" -v "${PWD}/tests:/app/tests" bot pytest -k speedup
docker compose run --rm -v "${PWD}/src:/app/src" bot ruff check .
docker compose run --rm -v "${PWD}/src:/app/src" bot mypy src/
```

The `tests/conftest.py` has an autouse fixture that isolates each test from
the real `.env` and developer env vars — tests that need config values set
them explicitly via `monkeypatch.setenv`.

**DB migrations:**
```powershell
alembic revision --autogenerate -m "message"
alembic upgrade head
```

## Architecture

### What the bot does
User uploads a training video with hashtags in the caption (`#squat day 1`).
Bot saves it per-user with tags. On demand, the bot compiles a ≤30s progress
reel by concatenating matching clips oldest→newest, asking the user for target
duration, date range, and whether to overlay the upload date on each clip.

### Code shape

In place today:
- `src/progress_tracker/db/` — `models.py`, async `session.py`, `repos.py`
  (`UserRepo`, `TagRepo`, `VideoRepo`, `CompilationRepo`).
- `src/progress_tracker/storage/` — `Storage` Protocol in `base.py`,
  `LocalStorage` in `local.py`. The Protocol exposes `write_path() -> Path`
  for writes and `open()` as an async context manager yielding a Path for
  reads. A future S3 backend uses tempdirs to satisfy that contract.
- `src/progress_tracker/services/ingest.py` — orchestrates the upload
  flow (parse hashtags → upsert user/tags → fetcher.fetch → write file →
  insert Video + VideoTag → count prior clips → fetcher.cleanup). Returns
  `IngestResult` or `None` (no hashtags = caller replies with hint).
- `src/progress_tracker/services/compiler.py` — ffmpeg compile orchestration.
- `src/progress_tracker/middlewares/db.py` — `DependenciesMiddleware`
  opens an `AsyncSession` per update and exposes `session`, `storage`,
  `fetcher`, and `session_factory` to handlers via `data[...]`. Commits
  on success, rolls back on exception.
- `src/progress_tracker/handlers/` — `start.py`, `video_upload.py`,
  `compile_flow.py`, each as a `make_router()` factory.
  `__init__.py::build_root_router` composes them.
- `src/progress_tracker/bot_api/session.py` — `CustomAiohttpSession` for
  SOCKS5 + HTTP Basic Auth.
- `src/progress_tracker/bot_api/fetcher.py` — `FileFetcher` Protocol with
  `RemoteFileFetcher` and `LocalFileFetcher`.
- `src/progress_tracker/video/` — `probe.py`, `compile.py`, `select.py`.

### Stack

Python 3.11+, aiogram 3.x, SQLAlchemy 2.0 async + asyncpg, Alembic,
pydantic-settings, structlog, ffmpeg (system binary). Deploy via Docker
Compose (`bot` + `db` services for dev; `bot-app` + `telegram-bot-api` +
`db` in `docker-compose.vds.yml` for production). Tests use pytest +
pytest-asyncio + testcontainers-postgres so tests hit a real Postgres,
not a mock.
