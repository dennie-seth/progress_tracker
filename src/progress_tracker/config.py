"""Runtime configuration loaded from environment / .env."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
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
    bot_api_username: str = Field(
        default="",
        description=(
            "HTTP Basic Auth username for the Bot API server (e.g. when "
            "fronted by caddy/nginx). Empty = no auth header sent."
        ),
    )
    bot_api_password: str = Field(
        default="",
        description="HTTP Basic Auth password for the Bot API server. Secret.",
    )

    # ---- VDS co-location: read source files directly off disk ----
    # When the bot-app and telegram-bot-api server share a filesystem (the
    # production deployment), `BOT_API_LOCAL_FILES=true` switches the file
    # fetcher from "download via HTTPS" to "copy from the path bot-api
    # returns." Off (the default) keeps the dev-from-home flow over SOCKS.
    bot_api_local_files: bool = Field(
        default=False,
        description=(
            "Read source files directly from disk (set true ONLY when "
            "bot-app and telegram-bot-api share a filesystem)."
        ),
    )
    bot_api_local_root: str = Field(
        default="/var/lib/telegram-bot-api",
        description=(
            "Trusted root for paths returned by getFile in --local mode. "
            "Validation requires the returned path sits under "
            "<root>/<bot_token>/."
        ),
    )

    @field_validator("socks_proxy_url", mode="before")
    @classmethod
    def _empty_str_is_none(cls, v: object) -> object:
        """Treat an empty-string env var as "unset" for the optional field.

        The production compose explicitly zeroes `SOCKS_PROXY_URL=""` to
        defend against operators copying a dev `.env` onto the VDS. Without
        this validator, that empty string would bind to `""` and code paths
        like `if settings.socks_proxy_url is None` would silently mis-branch
        even though `if settings.socks_proxy_url:` works.
        """
        if isinstance(v, str) and v == "":
            return None
        return v


def load_settings() -> Settings:
    """Instantiate Settings. Separated so tests can monkeypatch easily."""
    return Settings()  # type: ignore[call-arg]  # env provides bot_token
