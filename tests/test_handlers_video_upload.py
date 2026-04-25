"""Tests for the video upload handler.

The handler is thin orchestration around `ingest_video`. We patch the service
and assert the handler delegates correctly and produces the right replies.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from progress_tracker.handlers.video_upload import (
    NO_HASHTAG_HINT,
    on_video,
)
from progress_tracker.services.ingest import IngestResult


def _msg(*, caption: str | None) -> AsyncMock:
    msg = AsyncMock()
    msg.caption = caption
    msg.bot = AsyncMock()
    return msg


async def test_replies_when_caption_has_no_hashtag(monkeypatch: pytest.MonkeyPatch) -> None:
    msg = _msg(caption="just a video")
    called = AsyncMock()
    monkeypatch.setattr(
        "progress_tracker.handlers.video_upload.ingest_video", called
    )
    await on_video(msg, session=AsyncMock(), storage=AsyncMock())
    called.assert_not_awaited()
    msg.reply.assert_awaited_once()
    args, kwargs = msg.reply.await_args
    assert NO_HASHTAG_HINT in args[0]


async def test_replies_with_first_clip_message(monkeypatch: pytest.MonkeyPatch) -> None:
    msg = _msg(caption="#squat")
    fake_video = SimpleNamespace(id="uuid")
    result = IngestResult(video=fake_video, tag_names=["squat"], prior_count=0)
    monkeypatch.setattr(
        "progress_tracker.handlers.video_upload.ingest_video",
        AsyncMock(return_value=result),
    )
    await on_video(msg, session=AsyncMock(), storage=AsyncMock())

    msg.reply.assert_awaited_once()
    text = msg.reply.await_args.args[0]
    assert "Saved" in text
    assert "#squat" in text
    assert "first clip" in text.lower() or "first" in text.lower()


async def test_replies_with_prior_count_and_offers_reel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    msg = _msg(caption="#squat day 5")
    fake_video = SimpleNamespace(id="uuid")
    result = IngestResult(video=fake_video, tag_names=["squat"], prior_count=4)
    monkeypatch.setattr(
        "progress_tracker.handlers.video_upload.ingest_video",
        AsyncMock(return_value=result),
    )
    await on_video(msg, session=AsyncMock(), storage=AsyncMock())

    msg.reply.assert_awaited_once()
    text = msg.reply.await_args.args[0]
    assert "4" in text
    assert "#squat" in text
    # Prompts the user about the reel
    assert "reel" in text.lower() or "compile" in text.lower() or "progress" in text.lower()


async def test_returns_none_path_when_video_missing_caption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A video message arriving with no caption at all -> hint reply, no ingest."""
    msg = _msg(caption=None)
    called = AsyncMock()
    monkeypatch.setattr(
        "progress_tracker.handlers.video_upload.ingest_video", called
    )
    await on_video(msg, session=AsyncMock(), storage=AsyncMock())
    called.assert_not_awaited()
    msg.reply.assert_awaited_once()
    assert NO_HASHTAG_HINT in msg.reply.await_args.args[0]


def test_make_router_returns_video_router() -> None:
    from aiogram import Router

    from progress_tracker.handlers.video_upload import make_router

    r = make_router()
    assert isinstance(r, Router)
    assert r.name == "video_upload"
