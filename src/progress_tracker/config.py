"""Runtime configuration loaded from environment / .env."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings.

    Values are loaded from environment variables (a `.env` file is consulted
    when present). See `.env.example` for the full list.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: str = Field(..., description="Telegram bot token from @BotFather")
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/progress_tracker",
        description="SQLAlchemy async DSN",
    )
    media_dir: Path = Field(
        default=Path("./media"),
        description="Directory for locally stored videos",
    )
    log_level: str = Field(default="INFO", description="Python logging level name")

    # ---- Milestone 2.5: custom Bot API endpoint + SOCKS5 ----
    bot_api_url: str = Field(
        default="",
        description=(
            "Base URL of a custom Telegram Bot API server "
            "(e.g. http://host:8081). Empty = use cloud api.telegram.org."
        ),
    )
    socks_proxy_url: str | None = Field(
        default=None,
        description=(
            "SOCKS5 proxy for Telegram Bot API traffic, classic format "
            "'socks5://user:pass@host:port'. None = direct connection."
        ),
    )


def load_settings() -> Settings:
    """Instantiate Settings. Separated so tests can monkeypatch easily."""
    return Settings()  # type: ignore[call-arg]  # env provides bot_token
