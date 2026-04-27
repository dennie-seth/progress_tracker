"""Tests for the delete_video service.

Service-layer tests stub the storage and use a real Postgres for the row.
Handler-level wiring is covered separately in `test_handlers_delete_flow.py`.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import AsyncSession

from progress_tracker.db.repos import TagRepo, UserRepo, VideoRepo
from progress_tracker.services.delete_video import DeleteResult, delete_video
from progress_tracker.storage.local import LocalStorage


async def test_delete_video_removes_row_and_file(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """Happy path: row is gone, on-disk file is gone, returns deleted=True."""
    storage = LocalStorage(root=tmp_path)
    await UserRepo(db_session).upsert(user_id=1, username="u", first_name="U")
    [tag] = await TagRepo(db_session).upsert_many(user_id=1, names=["squat"])

    storage_key = "1/abc.mp4"
    target = await storage.write_path(storage_key)
    target.write_bytes(b"video-bytes")
    await storage.commit(storage_key)

    vid = await VideoRepo(db_session).create(
        id=uuid.uuid4(),
        user_id=1,
        telegram_file_id="t",
        storage_key=storage_key,
        duration_sec=Decimal("1"),
        tag_ids=[tag.id],
    )
    await db_session.commit()
    assert await storage.exists(storage_key)

    result = await delete_video(
        session=db_session, storage=storage, user_id=1, video_id=vid.id
    )
    await db_session.commit()

    assert isinstance(result, DeleteResult)
    assert result.deleted is True
    assert result.storage_key == storage_key
    assert not await storage.exists(storage_key)


async def test_delete_video_returns_false_when_video_not_found(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """Unknown / non-existent video id → deleted=False, storage untouched."""
    storage = LocalStorage(root=tmp_path)
    await UserRepo(db_session).upsert(user_id=1, username="u", first_name="U")
    await db_session.commit()

    result = await delete_video(
        session=db_session, storage=storage, user_id=1, video_id=uuid.uuid4()
    )
    assert result.deleted is False
    assert result.storage_key is None


async def test_delete_video_does_not_touch_storage_when_repo_returns_none(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """We only delete the file when the DB delete actually succeeded."""
    storage = AsyncMock()
    storage.delete = AsyncMock()

    await UserRepo(db_session).upsert(user_id=1, username="u", first_name="U")
    await db_session.commit()

    result = await delete_video(
        session=db_session, storage=storage, user_id=1, video_id=uuid.uuid4()
    )
    assert result.deleted is False
    storage.delete.assert_not_awaited()


async def test_delete_video_blocks_cross_user(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """User 2 attempting to delete user 1's video → deleted=False, file stays."""
    storage = LocalStorage(root=tmp_path)
    await UserRepo(db_session).upsert(user_id=1, username="u1", first_name="U1")
    await UserRepo(db_session).upsert(user_id=2, username="u2", first_name="U2")
    [tag] = await TagRepo(db_session).upsert_many(user_id=1, names=["squat"])

    storage_key = "1/abc.mp4"
    target = await storage.write_path(storage_key)
    target.write_bytes(b"x")
    await storage.commit(storage_key)

    vid = await VideoRepo(db_session).create(
        id=uuid.uuid4(),
        user_id=1,
        telegram_file_id="t",
        storage_key=storage_key,
        duration_sec=Decimal("1"),
        tag_ids=[tag.id],
    )
    await db_session.commit()

    result = await delete_video(
        session=db_session, storage=storage, user_id=2, video_id=vid.id
    )
    assert result.deleted is False
    assert await storage.exists(storage_key)


async def test_delete_video_swallows_storage_failure(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """If the row went away but the on-disk file delete fails, surface
    success — the row is gone and the file is now an orphan the operator
    can clean up. Re-raising would leave the user staring at an error
    despite the video already being out of the library."""
    real_storage = LocalStorage(root=tmp_path)
    await UserRepo(db_session).upsert(user_id=1, username="u", first_name="U")
    [tag] = await TagRepo(db_session).upsert_many(user_id=1, names=["squat"])

    storage_key = "1/abc.mp4"
    target = await real_storage.write_path(storage_key)
    target.write_bytes(b"x")
    await real_storage.commit(storage_key)

    vid = await VideoRepo(db_session).create(
        id=uuid.uuid4(),
        user_id=1,
        telegram_file_id="t",
        storage_key=storage_key,
        duration_sec=Decimal("1"),
        tag_ids=[tag.id],
    )
    await db_session.commit()

    class FlakyStorage:
        async def write_path(self, key: str) -> Path: ...
        async def commit(self, key: str) -> None: ...
        async def open(self, key: str): ...
        async def exists(self, key: str) -> bool: ...

        async def delete(self, key: str) -> None:
            raise RuntimeError("disk busy")

    result = await delete_video(
        session=db_session, storage=FlakyStorage(), user_id=1, video_id=vid.id
    )
    assert result.deleted is True
    assert result.storage_key == storage_key
