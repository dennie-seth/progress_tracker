FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ffmpeg is required by later milestones for video probing/compilation.
# Installing it now keeps the runtime image stable across milestones.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first for better layer caching.
COPY pyproject.toml README.md ./
COPY src ./src
# Editable install so that bind-mounting src/ at runtime hot-reloads the
# package without a rebuild. Dev extras (ruff/mypy/pytest/...) are included
# so the same image can run lint, type-check, and tests via
# `docker compose run --rm bot pytest`.
RUN pip install --upgrade pip && pip install -e ".[dev]"

# Alembic config + migration scripts are copied AFTER the pip install so
# changes here don't invalidate the dependency layer.
COPY alembic.ini ./
COPY migrations ./migrations

# Non-root user
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /data/media \
    && chown -R appuser:appuser /data
USER appuser

ENV MEDIA_DIR=/data/media

CMD ["python", "-m", "progress_tracker"]
