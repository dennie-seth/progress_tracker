# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

Early-alpha. **Milestone 1 (skeleton) is the only milestone implemented.** The
full design and roadmap live in the plan file at
`C:\Users\denni\.claude\plans\enumerated-twirling-glacier.md` — read it before
starting non-trivial work, it lists the target data model, ffmpeg strategy, and
build order (milestones 2–9 are not yet code).

What exists right now: aiogram bot that answers `/start` and `/help`. No DB
usage, no video handling, no FSM flows yet — those are milestones 2–6.

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
- **Clip trimming rule**: if `clip.duration <= target/N`, keep at full speed;
  otherwise speed up via `setpts=PTS/speed` + `atempo` (chain `atempo` filters
  when speed > 2.0). Never truncate — the user chose "speed up" over "crop".
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

### Code shape (once milestones land)

The eventual layout is described in the plan file. Near-term additions slot in
as follows:
- `src/progress_tracker/db/` — SQLAlchemy 2.0 async models, session factory,
  repositories (`VideoRepo`, `TagRepo`, `CompilationRepo`).
- `src/progress_tracker/handlers/video_upload.py` + `compile_flow.py` — the
  upload entry point and the multi-step compile FSM.
- `src/progress_tracker/video/` — `probe.py` (ffprobe), `compile.py` (filter
  graph builder — the most complex file), `overlay.py` (drawtext helpers).
- `src/progress_tracker/services/` — orchestration that the handlers call;
  keep handlers thin.

All new routers must be registered in `handlers/__init__.py::build_root_router`
— do not register routers directly on the Dispatcher in `bot.py`.

### Stack

Python 3.11+, aiogram 3.x, SQLAlchemy 2.0 async + asyncpg, Alembic,
pydantic-settings, structlog, ffmpeg (system binary). Deploy via Docker
Compose (`bot` + `db` services). Tests use pytest + pytest-asyncio +
testcontainers-postgres so tests hit a real Postgres, not a mock.
