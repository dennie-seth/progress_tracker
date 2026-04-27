"""Tests for DependenciesMiddleware: opens a session, injects storage, commits/rollbacks."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from progress_tracker.bot_api.fetcher import RemoteFileFetcher
from progress_tracker.db.session import create_session_factory
from progress_tracker.middlewares.db import DependenciesMiddleware
from progress_tracker.storage.local import LocalStorage


async def test_injects_session_storage_and_fetcher(
    db_engine: AsyncEngine, tmp_path: Path
) -> None:
    storage = LocalStorage(root=tmp_path)
    factory = create_session_factory(db_engine)
    fetcher = RemoteFileFetcher()
    mw = DependenciesMiddleware(
        session_factory=factory, storage=storage, fetcher=fetcher
    )

    captured: dict[str, Any] = {}

    async def handler(event: Any, data: dict[str, Any]) -> str:
        captured["session"] = data.get("session")
        captured["storage"] = data.get("storage")
        captured["fetcher"] = data.get("fetcher")
        return "ok"

    result = await mw(handler, SimpleNamespace(), {})
    assert result == "ok"
    assert captured["storage"] is storage
    assert captured["fetcher"] is fetcher
    assert captured["session"] is not None


async def test_commits_on_success(db_engine: AsyncEngine, tmp_path: Path) -> None:
    storage = LocalStorage(root=tmp_path)
    factory = create_session_factory(db_engine)

    commit_spy = AsyncMock()
    rollback_spy = AsyncMock()

    class FakeSession:
        async def __aenter__(self) -> "FakeSession":
            return self

        async def __aexit__(self, *exc: Any) -> None:
            pass

        async def commit(self) -> None:
            await commit_spy()

        async def rollback(self) -> None:
            await rollback_spy()

    fake_factory = MagicMock(return_value=FakeSession())
    mw = DependenciesMiddleware(  # type: ignore[arg-type]
        session_factory=fake_factory,
        storage=storage,
        fetcher=RemoteFileFetcher(),
    )

    async def handler(event: Any, data: dict[str, Any]) -> str:
        return "ok"

    await mw(handler, SimpleNamespace(), {})
    commit_spy.assert_awaited_once()
    rollback_spy.assert_not_awaited()


async def test_rolls_back_on_exception(tmp_path: Path) -> None:
    storage = LocalStorage(root=tmp_path)

    commit_spy = AsyncMock()
    rollback_spy = AsyncMock()

    class FakeSession:
        async def __aenter__(self) -> "FakeSession":
            return self

        async def __aexit__(self, *exc: Any) -> None:
            pass

        async def commit(self) -> None:
            await commit_spy()

        async def rollback(self) -> None:
            await rollback_spy()

    fake_factory = MagicMock(return_value=FakeSession())
    mw = DependenciesMiddleware(  # type: ignore[arg-type]
        session_factory=fake_factory,
        storage=storage,
        fetcher=RemoteFileFetcher(),
    )

    async def handler(event: Any, data: dict[str, Any]) -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await mw(handler, SimpleNamespace(), {})
    rollback_spy.assert_awaited_once()
    commit_spy.assert_not_awaited()


async def test_rolls_back_when_commit_itself_fails(tmp_path: Path) -> None:
    """Commit-failure must rollback so the session/connection is left clean."""
    storage = LocalStorage(root=tmp_path)
    rollback_spy = AsyncMock()

    class FakeSession:
        async def __aenter__(self) -> "FakeSession":
            return self

        async def __aexit__(self, *exc: Any) -> None:
            pass

        async def commit(self) -> None:
            raise RuntimeError("commit-failed")

        async def rollback(self) -> None:
            await rollback_spy()

    fake_factory = MagicMock(return_value=FakeSession())
    mw = DependenciesMiddleware(  # type: ignore[arg-type]
        session_factory=fake_factory,
        storage=storage,
        fetcher=RemoteFileFetcher(),
    )

    async def handler(event: Any, data: dict[str, Any]) -> str:
        return "ok"

    with pytest.raises(RuntimeError, match="commit-failed"):
        await mw(handler, SimpleNamespace(), {})
    rollback_spy.assert_awaited_once()
