"""Outer middleware that opens a DB session and exposes it (and storage) to handlers.

Handlers declare the deps they need by listing them in their signature, e.g.:

    async def on_video(message: Message, session: AsyncSession, storage: Storage) -> None:
        ...

aiogram resolves those kwargs from the `data` dict this middleware mutates.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from progress_tracker.storage.base import Storage


class DependenciesMiddleware(BaseMiddleware):
    """Opens a session per update, commits on success, rolls back on error.

    Storage is a long-lived singleton, so it's just stashed in `data`.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        storage: Storage,
    ) -> None:
        self._factory = session_factory
        self._storage = storage

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with self._factory() as session:
            data["session"] = session
            data["storage"] = self._storage
            try:
                result = await handler(event, data)
            except Exception:
                await session.rollback()
                raise
            await session.commit()
            return result
