"""Tests for progress_tracker.config."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from progress_tracker.config import Settings, load_settings


def test_missing_bot_token_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_bot_token_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "123:ABCDEF")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.bot_token == "123:ABCDEF"


def test_defaults_when_only_token_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "x")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.log_level == "INFO"
    assert s.media_dir == Path("./media")
    assert s.database_url.startswith("postgresql+asyncpg://")


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "x")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("MEDIA_DIR", "/srv/videos")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/db")

    s = Settings(_env_file=None)  # type: ignore[call-arg]

    assert s.log_level == "DEBUG"
    assert s.media_dir == Path("/srv/videos")
    assert s.database_url == "postgresql+asyncpg://u:p@h:5432/db"


def test_extra_env_vars_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unrelated env vars must not cause ValidationError (extra='ignore')."""
    monkeypatch.setenv("BOT_TOKEN", "x")
    monkeypatch.setenv("SOMETHING_UNRELATED", "foo")
    Settings(_env_file=None)  # type: ignore[call-arg]


def test_load_settings_returns_settings_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "abc")
    s = load_settings()
    assert isinstance(s, Settings)
    assert s.bot_token == "abc"
