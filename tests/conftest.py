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

import pytest

_PROJECT_ENV_VARS = ("BOT_TOKEN", "DATABASE_URL", "MEDIA_DIR", "LOG_LEVEL")
_ORIGINAL_DATABASE_URL = os.environ.get("DATABASE_URL")


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
