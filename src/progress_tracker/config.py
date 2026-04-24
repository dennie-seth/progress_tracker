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


def load_settings() -> Settings:
    """Instantiate Settings. Separated so tests can monkeypatch easily."""
    return Settings()  # type: ignore[call-arg]  # env provides bot_token
