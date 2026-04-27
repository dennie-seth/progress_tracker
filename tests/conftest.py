"""Shared pytest fixtures.

The autouse `_isolate_env` fixture prevents the real `.env` file and the
developer's environment variables from leaking into tests. Tests that need
config values set them explicitly via `monkeypatch.setenv`.

For tests that need a real Postgres, request the `postgres_url` fixture.
It exposes the `DATABASE_URL` captured at import time (before env isolation
kicks in) and skips the test if it isn't set. Inside docker compose the
`bot` service has `DATABASE_URL` pointing at the `db` service, so these
integration tests run automatically.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from progress_tracker.db.session import create_engine, create_session_factory

_PROJECT_ENV_VARS = (
    "BOT_TOKEN",
    "DATABASE_URL",
    "MEDIA_DIR",
    "LOG_LEVEL",
    "BOT_API_URL",
    "SOCKS_PROXY_URL",
    "BOT_API_USERNAME",
    "BOT_API_PASSWORD",
    "BOT_API_LOCAL_FILES",
    "BOT_API_LOCAL_ROOT",
)
_ORIGINAL_DATABASE_URL = os.environ.get("DATABASE_URL")
_TABLES_IN_TRUNCATE_ORDER = "users, tags, videos, video_tags, compilations"


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Run every test from a clean cwd with no project env vars set."""
    monkeypatch.chdir(tmp_path)
    for name in _PROJECT_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def postgres_url() -> str:
    """Real Postgres DSN for integration tests. Skips if unavailable."""
    if not _ORIGINAL_DATABASE_URL:
        pytest.skip("DATABASE_URL not set — run tests inside docker compose")
    return _ORIGINAL_DATABASE_URL


@pytest.fixture(scope="session")
def sample_clips(tmp_path_factory: pytest.TempPathFactory) -> list:
    """Three short MP4s with audio, generated once per session via ffmpeg lavfi.

    The clips have distinct durations (1s/2s/3s) so end-to-end compile tests
    can verify the speedup math actually changed the output duration. They
    use the standard 320x240 H.264 + AAC at 30fps so the normalizer does
    real work but the test runs in a few hundred milliseconds total.
    """
    import subprocess
    from pathlib import Path

    base = tmp_path_factory.mktemp("clips")
    paths: list[Path] = []
    for i, duration in enumerate([1, 2, 3]):
        # Pick a different solid colour per clip so a human inspecting the
        # compiled output can see them concatenated.
        color = ["red", "green", "blue"][i]
        out = base / f"clip_{i}_{duration}s.mp4"
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-f", "lavfi", "-i", f"color={color}:size=320x240:rate=30",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
                "-shortest", "-t", str(duration),
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                "-c:a", "aac",
                str(out),
            ],
            check=True,
        )
        paths.append(out)
    return paths


@pytest.fixture
async def db_engine(postgres_url: str) -> AsyncIterator[AsyncEngine]:
    """Async engine bound to the compose `db` service. Disposed after each test."""
    engine = create_engine(postgres_url)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(db_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Truncate every domain table, then yield a fresh AsyncSession.

    Tests can `await session.commit()` freely — the next test will start with
    empty tables again.
    """
    async with db_engine.begin() as conn:
        await conn.execute(
            text(f"TRUNCATE {_TABLES_IN_TRUNCATE_ORDER} RESTART IDENTITY CASCADE")
        )
    factory: async_sessionmaker[AsyncSession] = create_session_factory(db_engine)
    async with factory() as session:
        yield session


@pytest.fixture
def reset_root_logger():
    """Reset logging root handlers/level so logging tests don't leak into each other."""
    root = logging.getLogger()
    prev_handlers = root.handlers[:]
    prev_level = root.level
    root.handlers.clear()
    yield
    root.handlers.clear()
    root.handlers.extend(prev_handlers)
    root.setLevel(prev_level)
