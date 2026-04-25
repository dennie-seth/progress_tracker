"""Integration tests for services.compiler.compile_progress_reel.

Real Postgres for the data model + real ffmpeg for the compile + real
LocalStorage. The Telegram surface (Bot.send_video) is mocked.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from progress_tracker.db.models import Video
from progress_tracker.db.repos import TagRepo, UserRepo, VideoRepo
from progress_tracker.db.session import create_session_factory
from progress_tracker.services.compiler import compile_progress_reel
from progress_tracker.storage.local import LocalStorage


async def _seed_clips(
    *,
    session: AsyncSession,
    storage: LocalStorage,
    sample_clips: list[Path],
    user_id: int,
) -> int:
    """Seed user, tag, and three Video rows whose storage_keys point at the
    fixture clips. Returns the tag_id."""
    await UserRepo(session).upsert(user_id=user_id, username="u", first_name="U")
    [tag] = await TagRepo(session).upsert_many(user_id=user_id, names=["squat"])
    await session.commit()

    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    repo = VideoRepo(session)
    for i, clip in enumerate(sample_clips):
        # Copy fixture into storage so the compile can `Storage.open` it.
        key = f"{user_id}/clip_{i}.mp4"
        target = await storage.write_path(key)
        target.write_bytes(clip.read_bytes())
        await storage.commit(key)

        v = await repo.create(
            id=uuid.uuid4(),
            user_id=user_id,
            telegram_file_id=f"tg-{i}",
            storage_key=key,
            duration_sec=Decimal(str(i + 1)),
            tag_ids=[tag.id],
        )
        await session.execute(
            update(Video).where(Video.id == v.id).values(created_at=base + timedelta(days=i))
        )
    await session.commit()
    return tag.id


async def test_compile_progress_reel_produces_video_and_records_row(
    db_engine,
    sample_clips: list[Path],
    tmp_path: Path,
) -> None:
    storage = LocalStorage(root=tmp_path)
    factory: async_sessionmaker[AsyncSession] = create_session_factory(db_engine)

    async with factory() as setup_session:
        # Truncate first since this test doesn't request the truncating fixture.
        from sqlalchemy import text
        await setup_session.execute(
            text("TRUNCATE users, tags, videos, video_tags, compilations RESTART IDENTITY CASCADE")
        )
        await setup_session.commit()

    async with factory() as setup_session:
        tag_id = await _seed_clips(
            session=setup_session,
            storage=storage,
            sample_clips=sample_clips,
            user_id=42,
        )

    bot = AsyncMock()
    bot.send_video = AsyncMock()

    result = await compile_progress_reel(
        bot=bot,
        chat_id=999,
        user_id=42,
        tag_id=tag_id,
        target_duration=4,
        since=None,
        overlay_dates=False,
        session_factory=factory,
        storage=storage,
    )

    assert result is not None
    # Compilation row was inserted
    async with factory() as check:
        from sqlalchemy import select

        from progress_tracker.db.models import Compilation

        rows = (await check.execute(select(Compilation).where(Compilation.user_id == 42))).scalars().all()
        assert len(rows) == 1
        assert rows[0].tag_id == tag_id
        assert rows[0].duration_sec > 0

    # Storage holds the rendered file
    assert await storage.exists(result.storage_key)

    # Bot was asked to send the video, with explicit dimensions so the
    # chat preview gets the correct aspect ratio.
    bot.send_video.assert_awaited_once()
    kwargs = bot.send_video.await_args.kwargs
    assert kwargs.get("chat_id") == 999
    assert kwargs.get("width") == 1080
    assert kwargs.get("height") == 1920
    assert isinstance(kwargs.get("duration"), int)
    assert kwargs.get("supports_streaming") is True


async def test_compile_progress_reel_with_no_matching_clips_returns_none(
    db_engine,
    tmp_path: Path,
) -> None:
    """No clips matching the (user_id, tag_id, since) filter -> returns None,
    no compile, no DB row, no send_video."""
    storage = LocalStorage(root=tmp_path)
    factory = create_session_factory(db_engine)

    async with factory() as setup_session:
        from sqlalchemy import text
        await setup_session.execute(
            text("TRUNCATE users, tags, videos, video_tags, compilations RESTART IDENTITY CASCADE")
        )
        await UserRepo(setup_session).upsert(user_id=42, username="u", first_name="U")
        [tag] = await TagRepo(setup_session).upsert_many(user_id=42, names=["empty"])
        await setup_session.commit()
        tag_id = tag.id

    bot = AsyncMock()

    result = await compile_progress_reel(
        bot=bot,
        chat_id=999,
        user_id=42,
        tag_id=tag_id,
        target_duration=10,
        since=None,
        overlay_dates=False,
        session_factory=factory,
        storage=storage,
    )

    assert result is None
    bot.send_video.assert_not_awaited()
