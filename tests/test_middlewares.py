"""Tests for DependenciesMiddleware: opens a session, injects storage, commits/rollbacks."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar
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
    # `db_engine` is requested only to share the test-scoped event loop; the
    # actual factory is the FakeSession-returning one below.
    _ = db_engine

    commit_spy = AsyncMock()
    rollback_spy = AsyncMock()

    class FakeSession:
        # ClassVar-form: dict-typed sentinel shared across instances; the
        # middleware only reads `.get("dirty_users", set())` so a single empty
        # dict is fine for these unit tests.
        info: ClassVar[dict[str, Any]] = {}

        async def __aenter__(self) -> FakeSession:
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
        # ClassVar-form: dict-typed sentinel shared across instances; the
        # middleware only reads `.get("dirty_users", set())` so a single empty
        # dict is fine for these unit tests.
        info: ClassVar[dict[str, Any]] = {}

        async def __aenter__(self) -> FakeSession:
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
        # ClassVar-form: dict-typed sentinel shared across instances; the
        # middleware only reads `.get("dirty_users", set())` so a single empty
        # dict is fine for these unit tests.
        info: ClassVar[dict[str, Any]] = {}

        async def __aenter__(self) -> FakeSession:
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


async def test_dumps_manifest_after_successful_commit(
    db_engine: AsyncEngine, tmp_path: Path
) -> None:
    """Services mark `session.info["dirty_users"]`; the middleware dumps
    each marked user's manifest in a fresh session after commit succeeds."""
    from progress_tracker.db.repos import UserRepo

    storage = LocalStorage(root=tmp_path)
    factory = create_session_factory(db_engine)
    mw = DependenciesMiddleware(
        session_factory=factory, storage=storage, fetcher=RemoteFileFetcher()
    )

    async def handler(event: Any, data: dict[str, Any]) -> str:
        s = data["session"]
        await UserRepo(s).upsert(user_id=42, username="a", first_name="A")
        s.info.setdefault("dirty_users", set()).add(42)
        return "ok"

    await mw(handler, SimpleNamespace(), {})

    manifest_path = tmp_path / "42" / "manifest.json"
    assert manifest_path.exists()


async def test_does_not_dump_manifest_after_rollback(
    db_engine: AsyncEngine, tmp_path: Path
) -> None:
    """When the handler raises (rollback path), the manifest must NOT be
    written — otherwise a phantom-state file would haunt the next recovery."""
    from progress_tracker.db.repos import UserRepo

    storage = LocalStorage(root=tmp_path)
    factory = create_session_factory(db_engine)
    mw = DependenciesMiddleware(
        session_factory=factory, storage=storage, fetcher=RemoteFileFetcher()
    )

    async def handler(event: Any, data: dict[str, Any]) -> None:
        s = data["session"]
        await UserRepo(s).upsert(user_id=43, username="b", first_name="B")
        s.info.setdefault("dirty_users", set()).add(43)
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await mw(handler, SimpleNamespace(), {})

    assert not (tmp_path / "43" / "manifest.json").exists()
