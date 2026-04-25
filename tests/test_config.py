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


# ---------- Milestone 2.5: remote Bot API over SOCKS5 ----------


def test_bot_api_url_defaults_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "x")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.bot_api_url == ""


def test_bot_api_url_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "x")
    monkeypatch.setenv("BOT_API_URL", "http://my-bot-api:8081")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.bot_api_url == "http://my-bot-api:8081"


def test_socks_proxy_url_defaults_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "x")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.socks_proxy_url is None


def test_socks_proxy_url_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "x")
    monkeypatch.setenv("SOCKS_PROXY_URL", "socks5://u:p@proxy:1080")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.socks_proxy_url == "socks5://u:p@proxy:1080"


def test_bot_api_and_socks_are_independent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neither setting requires the other — they're orthogonal."""
    monkeypatch.setenv("BOT_TOKEN", "x")
    # Only socks set — should not raise
    monkeypatch.setenv("SOCKS_PROXY_URL", "socks5://u:p@proxy:1080")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.bot_api_url == ""
    assert s.socks_proxy_url == "socks5://u:p@proxy:1080"


# ---------- Milestone 2.5 follow-up: HTTP Basic Auth on Bot API server ----------


def test_bot_api_username_defaults_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "x")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.bot_api_username == ""
    assert s.bot_api_password == ""


def test_bot_api_basic_auth_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "x")
    monkeypatch.setenv("BOT_API_USERNAME", "progress_bot")
    monkeypatch.setenv("BOT_API_PASSWORD", "s3cret")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.bot_api_username == "progress_bot"
    assert s.bot_api_password == "s3cret"
