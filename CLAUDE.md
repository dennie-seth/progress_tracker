# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

Early-alpha. Milestones 1, 2, 2.5 and 3 are implemented. Roadmap at
`C:\Users\denni\.claude\plans\enumerated-twirling-glacier.md`; current
substep plan at `C:\Users\denni\.claude\plans\milestone-2.5-bot-api-server.md`
(milestone 2.5 is shipped, kept for reference).

What works right now:
- `/start` and `/help` reply with a welcome message.
- Postgres with the full schema (users / tags / videos / video_tags /
  compilations) — Alembic runs `upgrade head` on every bot startup.
- Optional routing through a remote `telegram-bot-api` server via SOCKS5
  (controlled by `BOT_API_URL` + `SOCKS_PROXY_URL`).
- Video upload pipeline: user sends a video with hashtags in the caption,
  bot downloads it (through the SOCKS tunnel if configured), saves to
  `LocalStorage`, persists `User`/`Tag`/`Video`/`VideoTag` rows, and replies
  with a saved-confirmation that includes how many prior clips share the
  same tags.

Not yet built: compile FSM (milestone 5), ffmpeg compiler (milestone 6),
history/library commands (milestone 4 partials), tests/CI workflow file
(milestone 8). ffprobe isn't wired — for now we trust Telegram's `Video`
duration/width/height fields. Add a probe step in milestone 4 if we need
fps or accurate duration.

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

**TDD is the rule here.** For any new function/class/handler/service, write a
failing unit test first, confirm it fails for the right reason, then
implement. Declarative SQLAlchemy models are the one allowed exception.

**DB migrations** (once Alembic is wired in milestone 2):
```powershell
alembic revision --autogenerate -m "message"
alembic upgrade head
```

## Secrets

`.env` holds the real `BOT_TOKEN` and is gitignored. `.env.example` is the
committed template and must stay blank. Never move secrets into `.env.example`.

## Architecture

### What the bot does
User uploads a training video with hashtags in the caption (`#squat day 1`).
Bot saves it per-user with tags. On demand, the bot compiles a ≤30s progress
reel by concatenating matching clips oldest→newest, asking the user for target
duration, date range, and whether to overlay the upload date on each clip.

### Key design decisions (not obvious from code)

- **Tags come from hashtags in the Telegram caption**, not an interactive
  prompt. The ingest path parses `#([\w\-]+)` from captions; empty caption =
  reject the upload with a help message. This keeps the happy path one-message.
- **Tags are per-user.** There is no shared library; `tags` has `UNIQUE(user_id,
  name)`. Do not add cross-user tag sharing without explicit request.
- **ffmpeg is invoked as a subprocess via `asyncio.create_subprocess_exec`**, not
  via MoviePy or PyAV. The compiler builds one big `-filter_complex` graph
  (normalize → optional `setpts`/`atempo` speedup → optional `drawtext`
  overlay → `concat`) so everything runs in a single ffmpeg process. Don't
  reintroduce MoviePy.
- **Bot API endpoint + SOCKS5 routing** (milestone 2.5 onward). `build_bot()`
  inspects `BOT_API_URL` (custom `telegram-bot-api` server, empty = cloud) and
  `SOCKS_PROXY_URL` (`socks5://user:pass@host:port`, None = direct) and swaps
  in `SocksAiohttpSession` (extends aiogram's `AiohttpSession`, replaces the
  default `TCPConnector` with `aiohttp_socks.ProxyConnector.from_url`) when a
  proxy is set. aiohttp does not support SOCKS5 natively — keep the custom
  session. When switching a bot from cloud api.telegram.org to a custom server,
  the operator must run `curl "https://api.telegram.org/bot<TOKEN>/logOut"`
  once before the first connection.
- **Clip trimming rule**: if `clip.duration <= target/N`, keep at full speed;
  otherwise speed up via `setpts=PTS/speed` + `atempo` (chain `atempo` filters
  when speed > 2.0). Never truncate — the user chose "speed up" over "crop".
- **Clip *selection* rule (milestone 6)**: don't include every matching clip;
  always include the **oldest** and the **newest**, plus a small number of
  **middle** clips sampled at random from the rest. The middle count scales
  with how many clips are available so a long history doesn't drown the
  oldest/newest signal:
    - `N <= 4` clips matching: include all
    - `5 <= N <= 9`: oldest + 2 middle (random) + newest = 4
    - `10 <= N <= 19`: oldest + 3 middle (random) + newest = 5
    - `N >= 20`: oldest + 3 middle (random) + newest = 5 (cap at 5)
  Middle picks come from `videos[1:-1]` chronologically; a fixed RNG seed is
  fine for reproducibility in tests, but production uses a fresh `random`.
  This is what the user asked for; revisit only if they request it.
- **Graceful shutdown**: `dp.start_polling(handle_signals=True)` installs
  POSIX SIGINT/SIGTERM handlers, so `docker compose stop` unwinds cleanly
  through the `finally` block in `__main__._run` (closes the bot's HTTP
  session, disposes the DB engine, logs `shutting down` / `bot stopped`).
  Don't replace this with a manual signal handler.
- **Storage is behind a `Storage` Protocol** (`storage/base.py` → `LocalStorage`
  now, `S3Storage` stub for later). ffmpeg needs real filesystem paths, so the
  Protocol exposes `open(key) -> AsyncContextManager[Path]`. `LocalStorage`
  yields the real path; `S3Storage` would download to a tempdir. Preserve this
  contract when adding backends.
- **FSM uses aiogram `MemoryStorage`.** Fine for a single-instance bot. Switch
  to Redis only when adding multi-replica deploys.
- **Long compilations run in-process** via `asyncio.create_task` with a
  status-editing message. A queue worker (arq/Celery) is a later concern —
  keep service-layer functions queue-agnostic so it can be added without
  rewriting call sites.

### Code shape

In place today:
- `src/progress_tracker/db/` — `models.py`, async `session.py`, `repos.py`
  (`UserRepo`, `TagRepo`, `VideoRepo`).
- `src/progress_tracker/storage/` — `Storage` Protocol in `base.py`,
  `LocalStorage` in `local.py`. ffmpeg needs real filesystem paths, so
  the Protocol exposes `write_path() -> Path` for writes and `open()` as
  an async context manager yielding a Path for reads. A future S3 backend
  uses tempdirs to satisfy that contract.
- `src/progress_tracker/services/ingest.py` — orchestrates the upload
  flow (parse hashtags → upsert user/tags → bot.download → write file →
  insert Video + VideoTag → count prior clips). Returns `IngestResult`
  or `None` (no hashtags = caller replies with hint).
- `src/progress_tracker/middlewares/db.py` — `DependenciesMiddleware`
  opens an `AsyncSession` per update, exposes it and `Storage` to handlers
  via `data["session"]` / `data["storage"]`, commits on success / rolls
  back on exception.
- `src/progress_tracker/handlers/` — `start.py` and `video_upload.py`,
  each as a `make_router()` factory. `__init__.py::build_root_router`
  composes them.
- `src/progress_tracker/bot_api/session.py` — `SocksAiohttpSession` for
  the SOCKS5 transport (milestone 2.5).

Coming next:
- `src/progress_tracker/handlers/compile_flow.py` — multi-step FSM.
- `src/progress_tracker/video/` — `probe.py`, `compile.py`, `overlay.py`.

All new routers must be registered in `handlers/__init__.py::build_root_router`
— do not register routers directly on the Dispatcher in `bot.py`.

### Stack

Python 3.11+, aiogram 3.x, SQLAlchemy 2.0 async + asyncpg, Alembic,
pydantic-settings, structlog, ffmpeg (system binary). Deploy via Docker
Compose (`bot` + `db` services). Tests use pytest + pytest-asyncio +
testcontainers-postgres so tests hit a real Postgres, not a mock.
