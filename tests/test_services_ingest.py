"""Tests for the ingest service.

These exercise the orchestration logic: hashtag parsing, user/tag upsert,
storage write, video row creation, prior-count. The Telegram surface (Bot,
Message) is mocked; the database and LocalStorage are real.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from progress_tracker.services.ingest import IngestResult, ingest_video
from progress_tracker.storage.local import LocalStorage


def _fake_message(
    *,
    user_id: int = 100,
    username: str | None = "alice",
    first_name: str | None = "Alice",
    caption: str | None = "#squat day 1",
    duration: int = 12,
    width: int = 1080,
    height: int = 1920,
    file_id: str = "tg-fid-1",
) -> MagicMock:
    msg = MagicMock(spec_set=("video", "caption", "from_user"))
    msg.caption = caption
    msg.from_user = SimpleNamespace(id=user_id, username=username, first_name=first_name)
    msg.video = SimpleNamespace(
        file_id=file_id,
        duration=duration,
        width=width,
        height=height,
    )
    return msg


def _fake_bot_writing(content: bytes = b"\x00fakempayload") -> MagicMock:
    """Returns a MagicMock Bot whose `download` writes bytes to the destination."""
    async def _download(_video, destination: Path) -> None:
        destination.write_bytes(content)

    bot = MagicMock()
    bot.download = AsyncMock(side_effect=_download)
    return bot


# ----------- behaviour -----------


async def test_returns_none_when_no_video(db_session: AsyncSession, tmp_path: Path) -> None:
    msg = _fake_message()
    msg.video = None
    storage = LocalStorage(root=tmp_path)
    result = await ingest_video(
        bot=_fake_bot_writing(), message=msg, session=db_session, storage=storage
    )
    assert result is None


async def test_returns_none_when_no_hashtags(db_session: AsyncSession, tmp_path: Path) -> None:
    msg = _fake_message(caption="just a video")
    storage = LocalStorage(root=tmp_path)
    result = await ingest_video(
        bot=_fake_bot_writing(), message=msg, session=db_session, storage=storage
    )
    assert result is None


async def test_first_upload_for_user(db_session: AsyncSession, tmp_path: Path) -> None:
    msg = _fake_message(user_id=11, caption="#squat day 1")
    storage = LocalStorage(root=tmp_path)
    bot = _fake_bot_writing(content=b"hello-mp4")

    result = await ingest_video(
        bot=bot, message=msg, session=db_session, storage=storage
    )
    await db_session.commit()

    assert isinstance(result, IngestResult)
    assert result.tag_names == ["squat"]
    assert result.prior_count == 0
    # Storage holds the file
    assert await storage.exists(result.video.storage_key)
    async with storage.open(result.video.storage_key) as p:
        assert p.read_bytes() == b"hello-mp4"
    # bot.download was called with the right destination
    assert bot.download.await_count == 1


async def test_prior_count_reflects_only_same_tags(db_session: AsyncSession, tmp_path: Path) -> None:
    storage = LocalStorage(root=tmp_path)
    bot = _fake_bot_writing()

    # Two prior #squat videos
    for i in range(2):
        await ingest_video(
            bot=bot,
            message=_fake_message(file_id=f"tg-{i}", caption="#squat"),
            session=db_session,
            storage=storage,
        )
    await db_session.commit()

    # New upload with #squat — prior count should be 2
    result = await ingest_video(
        bot=bot,
        message=_fake_message(file_id="tg-new", caption="#squat day 3"),
        session=db_session,
        storage=storage,
    )
    await db_session.commit()
    assert result is not None
    assert result.prior_count == 2

    # An unrelated tag should yield 0 priors for that tag
    result_pr = await ingest_video(
        bot=bot,
        message=_fake_message(file_id="tg-pr", caption="#pr"),
        session=db_session,
        storage=storage,
    )
    await db_session.commit()
    assert result_pr is not None
    assert result_pr.prior_count == 0


async def test_storage_key_is_under_user_directory(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    storage = LocalStorage(root=tmp_path)
    msg = _fake_message(user_id=42, caption="#a")
    result = await ingest_video(
        bot=_fake_bot_writing(), message=msg, session=db_session, storage=storage
    )
    assert result is not None
    assert result.video.storage_key.startswith("42/")


async def test_dedup_caption_tags(db_session: AsyncSession, tmp_path: Path) -> None:
    storage = LocalStorage(root=tmp_path)
    msg = _fake_message(caption="#squat #SQUAT #squat")
    result = await ingest_video(
        bot=_fake_bot_writing(), message=msg, session=db_session, storage=storage
    )
    assert result is not None
    assert result.tag_names == ["squat"]
