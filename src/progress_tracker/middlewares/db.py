"""Outer middleware that opens a DB session and exposes the per-handler deps.

Handlers declare the deps they need by listing them in their signature, e.g.:

    async def on_video(
        message: Message,
        session: AsyncSession,
        storage: Storage,
        fetcher: FileFetcher,
    ) -> None:
        ...

aiogram resolves those kwargs from the `data` dict this middleware mutates.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from progress_tracker.bot_api.fetcher import FileFetcher
from progress_tracker.services.persistence import dump_user_manifest
from progress_tracker.storage.base import Storage

_log = structlog.get_logger("progress_tracker.middleware")


class DependenciesMiddleware(BaseMiddleware):
    """Opens a session per update, commits on success, rolls back on error.

    Storage and the file fetcher are long-lived singletons, so they're just
    stashed in `data`.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        storage: Storage,
        fetcher: FileFetcher,
    ) -> None:
        self._factory = session_factory
        self._storage = storage
        self._fetcher = fetcher

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with self._factory() as session:
            data["session"] = session
            data["storage"] = self._storage
            data["fetcher"] = self._fetcher
            # Expose the factory too — long-running background tasks
            # (e.g., the compile pipeline) need to open their own session
            # because the per-update one is closed when this middleware
            # returns.
            data["session_factory"] = self._factory
            try:
                result = await handler(event, data)
                await session.commit()
            except Exception:
                # Rollback on either handler failure OR commit failure — the
                # latter can happen on deadlock / connection loss / deferred
                # constraint, and leaves the session unusable otherwise.
                await session.rollback()
                raise
            else:
                # Post-commit only: dump manifests for any users whose state
                # this request mutated. Services mark dirty users via
                # `session.info["dirty_users"]`. Fresh session for the dump so
                # the manifest reflects the just-committed state, never an
                # uncommitted one. Per-user failures are logged but never
                # bubble up — the user-facing ingest/delete already
                # succeeded.
                dirty_users: set[int] = session.info.get("dirty_users", set())
                for user_id in dirty_users:
                    try:
                        async with self._factory() as fresh:
                            await dump_user_manifest(
                                fresh, self._storage, user_id=user_id
                            )
                    except Exception:
                        _log.warning(
                            "manifest dump failed",
                            user_id=user_id,
                            exc_info=True,
                        )
            return result
