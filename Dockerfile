# ==========================================
# Stage 1: base — system deps + project source
# ==========================================
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive

# ffmpeg: video probing + compilation. tini: PID 1 reaper so SIGTERM from
# `docker compose stop` reaches Python cleanly even if the entrypoint chain
# changes (the project rule in `.rules/general.md` already mandates a
# graceful shutdown handler in __main__._run; tini is the belt-and-braces).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependency manifests first so the install layer caches across source changes.
COPY pyproject.toml README.md ./
COPY src ./src

# Alembic config + migration scripts. Copied AFTER source so source-only
# edits don't invalidate this layer.
COPY alembic.ini ./
COPY migrations ./migrations

# Non-root user matching the bot-api image's UID 1000 — the production
# deploy bind-mounts the bot-api server's data directory read-only into
# this container, and aligned UIDs mean the bot can read those files
# without the operator chowning anything.
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /data/media \
    && chown -R appuser:appuser /data /app

ENV MEDIA_DIR=/data/media

# Tini is PID 1 so SIGTERM is delivered cleanly to the CMD even when the
# CMD itself is `sh -c "alembic upgrade head && exec python ..."`. PATH
# lookup (rather than `/usr/bin/tini`) keeps this resilient across base-
# image bumps where the binary's absolute path can shift.
ENTRYPOINT ["tini", "--"]


# ==========================================
# Stage 2: prod — install runtime deps only
# ==========================================
# This is the default target used by `docker-compose.yml` on the VDS. It
# ships only what the bot needs to run; ruff/mypy/pytest/testcontainers
# stay out of the production image (smaller surface, faster pulls).
FROM base AS prod

RUN pip install --upgrade pip && pip install -e .

USER appuser
CMD ["python", "-m", "progress_tracker"]


# ==========================================
# Stage 3: dev — adds [dev] extras for lint / typecheck / tests
# ==========================================
# Used by `docker-compose.dev.yml`. The same image runs the bot AND lint /
# mypy / pytest via `docker compose -f docker-compose.dev.yml run --rm
# bot-app <cmd>`.
FROM base AS dev

RUN pip install --upgrade pip && pip install -e ".[dev]"

USER appuser
CMD ["python", "-m", "progress_tracker"]
