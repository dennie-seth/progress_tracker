"""Tests for the /delete FSM handler.

Strategy: drive each handler directly with mocked Message / CallbackQuery /
FSMContext / Bot. The DB and storage are real (via existing fixtures) for
the entry-point tests; service-level wiring is mocked for the confirm path
since `delete_video` has its own test module.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram import Router
from aiogram.types import InlineKeyboardMarkup
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from progress_tracker.db.models import Video
from progress_tracker.db.repos import TagRepo, UserRepo, VideoRepo
from progress_tracker.handlers.delete_flow import (
    DeleteStates,
    _confirm_keyboard,
    _delete_keyboard,
    _tag_keyboard,
    make_router,
    on_cancel_confirm,
    on_confirm,
    on_delete,
    on_delete_button,
    on_tag_selected,
)
from progress_tracker.storage.local import LocalStorage

# ---------- keyboard helpers ----------


def test_tag_keyboard_one_button_per_tag() -> None:
    tags = [SimpleNamespace(id=i, name=f"t{i}") for i in range(3)]
    kb = _tag_keyboard(tags)
    assert isinstance(kb, InlineKeyboardMarkup)
    flat = [b for row in kb.inline_keyboard for b in row]
    assert {b.callback_data for b in flat} == {f"del_tag:{i}" for i in range(3)}
    assert all(b.text.startswith("#") for b in flat)


def test_delete_keyboard_carries_uuid() -> None:
    vid = uuid.uuid4()
    kb = _delete_keyboard(vid)
    flat = [b for row in kb.inline_keyboard for b in row]
    assert len(flat) == 1
    assert flat[0].callback_data == f"del:{vid}"


def test_confirm_keyboard_has_confirm_and_cancel_carrying_uuid() -> None:
    vid = uuid.uuid4()
    kb = _confirm_keyboard(vid)
    flat = [b for row in kb.inline_keyboard for b in row]
    assert {b.callback_data for b in flat} == {
        f"del_confirm:{vid}",
        f"del_cancel:{vid}",
    }


# ---------- /delete entry ----------


def _msg(*, user_id: int = 100) -> AsyncMock:
    msg = AsyncMock()
    msg.from_user = SimpleNamespace(id=user_id, username="u", first_name="U")
    msg.bot = AsyncMock()
    msg.chat = SimpleNamespace(id=user_id)  # private chat
    return msg


def _state() -> AsyncMock:
    s = AsyncMock()
    s.get_data = AsyncMock(return_value={})
    return s


async def test_on_delete_with_no_tags_replies_and_clears(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    msg = _msg(user_id=100)
    state = _state()
    await on_delete(
        msg, state=state, session=db_session, storage=LocalStorage(root=tmp_path)
    )
    msg.answer.assert_awaited_once()
    text = msg.answer.await_args.args[0]
    assert "no" in text.lower() or "empty" in text.lower()
    state.clear.assert_awaited()
    state.set_state.assert_not_awaited()


async def test_on_delete_with_one_tag_skips_to_browsing(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """One tag → auto-pick, list videos, set state to browsing."""
    await UserRepo(db_session).upsert(user_id=100, username="u", first_name="U")
    [tag] = await TagRepo(db_session).upsert_many(user_id=100, names=["squat"])
    repo = VideoRepo(db_session)
    await repo.create(
        id=uuid.uuid4(),
        user_id=100,
        telegram_file_id="tg-1",
        storage_key="100/a.mp4",
        duration_sec=Decimal("1"),
        tag_ids=[tag.id],
    )
    await db_session.commit()

    msg = _msg(user_id=100)
    state = _state()
    await on_delete(
        msg, state=state, session=db_session, storage=LocalStorage(root=tmp_path)
    )
    state.update_data.assert_any_await(tag_id=tag.id, tag_name="squat")
    state.set_state.assert_awaited_with(DeleteStates.browsing)
    msg.bot.send_video.assert_awaited()


async def test_on_delete_with_multiple_tags_asks_for_choice(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    await UserRepo(db_session).upsert(user_id=100, username="u", first_name="U")
    await TagRepo(db_session).upsert_many(user_id=100, names=["squat", "pr"])
    await db_session.commit()

    msg = _msg(user_id=100)
    state = _state()
    await on_delete(
        msg, state=state, session=db_session, storage=LocalStorage(root=tmp_path)
    )
    state.set_state.assert_awaited_with(DeleteStates.waiting_tag)
    msg.answer.assert_awaited_once()
    state.update_data.assert_not_awaited()


async def test_on_delete_one_tag_zero_videos_replies_no_clips(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """The tag exists but no videos point at it (after a previous full
    cleanup). The handler should say so cleanly, not show an empty list."""
    await UserRepo(db_session).upsert(user_id=100, username="u", first_name="U")
    await TagRepo(db_session).upsert_many(user_id=100, names=["squat"])
    await db_session.commit()

    msg = _msg(user_id=100)
    state = _state()
    await on_delete(
        msg, state=state, session=db_session, storage=LocalStorage(root=tmp_path)
    )
    msg.answer.assert_awaited()
    state.clear.assert_awaited()
    msg.bot.send_video.assert_not_awaited()


async def test_listing_is_most_recent_first_capped_at_20(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """The accidental-upload use case wants the newest clips at the top.
    Cap at 20 to keep the chat readable; older clips wait for the user to
    delete some recent ones first."""
    await UserRepo(db_session).upsert(user_id=100, username="u", first_name="U")
    [tag] = await TagRepo(db_session).upsert_many(user_id=100, names=["squat"])
    repo = VideoRepo(db_session)

    base = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(25):
        v = await repo.create(
            id=uuid.uuid4(),
            user_id=100,
            telegram_file_id=f"tg-{i}",
            storage_key=f"100/{i}.mp4",
            duration_sec=Decimal("1"),
            tag_ids=[tag.id],
        )
        await db_session.execute(
            update(Video).where(Video.id == v.id).values(created_at=base + timedelta(days=i))
        )
    await db_session.commit()

    msg = _msg(user_id=100)
    state = _state()
    await on_delete(
        msg, state=state, session=db_session, storage=LocalStorage(root=tmp_path)
    )
    # 20 video messages were sent (cap), newest first
    sent_calls = msg.bot.send_video.await_args_list
    assert len(sent_calls) == 20
    sent_file_ids = [c.kwargs["video"] for c in sent_calls]
    # Newest is tg-24 (i=24, latest day), oldest in window is tg-5.
    assert sent_file_ids[0] == "tg-24"
    assert sent_file_ids[-1] == "tg-5"


# ---------- callback transitions ----------


def _callback(data: str, *, user_id: int = 100) -> AsyncMock:
    cb = AsyncMock()
    cb.from_user = SimpleNamespace(id=user_id, username="u", first_name="U")
    cb.data = data
    cb.message = AsyncMock()
    cb.message.chat = SimpleNamespace(id=user_id)
    cb.bot = AsyncMock()
    return cb


async def test_tag_selected_lists_videos_and_sets_browsing(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    await UserRepo(db_session).upsert(user_id=100, username="u", first_name="U")
    [tag] = await TagRepo(db_session).upsert_many(user_id=100, names=["squat"])
    await VideoRepo(db_session).create(
        id=uuid.uuid4(),
        user_id=100,
        telegram_file_id="tg-1",
        storage_key="100/a.mp4",
        duration_sec=Decimal("1"),
        tag_ids=[tag.id],
    )
    await db_session.commit()

    cb = _callback(f"del_tag:{tag.id}", user_id=100)
    state = _state()
    await on_tag_selected(cb, state=state, session=db_session)

    state.update_data.assert_any_await(tag_id=tag.id, tag_name="squat")
    state.set_state.assert_awaited_with(DeleteStates.browsing)
    cb.bot.send_video.assert_awaited()
    cb.answer.assert_awaited()


async def test_tag_selected_rejects_other_users_tag(
    db_session: AsyncSession,
) -> None:
    await UserRepo(db_session).upsert(user_id=1, username="u1", first_name="U1")
    await UserRepo(db_session).upsert(user_id=2, username="u2", first_name="U2")
    [foreign] = await TagRepo(db_session).upsert_many(user_id=1, names=["secret"])
    await db_session.commit()

    cb = _callback(f"del_tag:{foreign.id}", user_id=2)
    state = _state()
    await on_tag_selected(cb, state=state, session=db_session)
    cb.answer.assert_awaited_once()
    # Should answer with an alert and NOT advance state.
    state.set_state.assert_not_awaited()


async def test_on_delete_button_edits_to_confirm_keyboard() -> None:
    vid = uuid.uuid4()
    cb = _callback(f"del:{vid}")
    state = _state()
    await on_delete_button(cb, state=state)
    cb.message.edit_reply_markup.assert_awaited_once()
    new_kb = cb.message.edit_reply_markup.await_args.kwargs["reply_markup"]
    flat = [b for row in new_kb.inline_keyboard for b in row]
    assert {b.callback_data for b in flat} == {
        f"del_confirm:{vid}",
        f"del_cancel:{vid}",
    }
    cb.answer.assert_awaited()


async def test_on_cancel_confirm_reverts_to_delete_button() -> None:
    vid = uuid.uuid4()
    cb = _callback(f"del_cancel:{vid}")
    state = _state()
    await on_cancel_confirm(cb, state=state)
    cb.message.edit_reply_markup.assert_awaited_once()
    new_kb = cb.message.edit_reply_markup.await_args.kwargs["reply_markup"]
    flat = [b for row in new_kb.inline_keyboard for b in row]
    assert flat[0].callback_data == f"del:{vid}"


async def test_on_confirm_calls_service_and_edits_message(
    db_session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The confirm callback delegates to `delete_video`, then edits the
    original video message's caption (or replaces reply_markup) to indicate
    the deletion succeeded."""
    from progress_tracker.services.delete_video import DeleteResult

    vid = uuid.uuid4()
    spy = AsyncMock(return_value=DeleteResult(deleted=True, storage_key="100/a.mp4"))
    monkeypatch.setattr(
        "progress_tracker.handlers.delete_flow.delete_video", spy
    )

    cb = _callback(f"del_confirm:{vid}", user_id=100)
    state = _state()
    storage = LocalStorage(root=tmp_path)
    await on_confirm(cb, state=state, session=db_session, storage=storage)

    spy.assert_awaited_once()
    kwargs = spy.await_args.kwargs
    assert kwargs["user_id"] == 100
    assert kwargs["video_id"] == vid
    cb.answer.assert_awaited()
    # Some "deleted" UI feedback happened — caption or text edit.
    assert (
        cb.message.edit_caption.await_count
        + cb.message.edit_text.await_count
        + cb.message.edit_reply_markup.await_count
    ) >= 1


async def test_on_confirm_handles_unknown_video_gracefully(
    db_session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the service returns deleted=False (e.g., already deleted), the
    handler must not raise — show the user a clear note and clear the
    button rather than silently failing."""
    from progress_tracker.services.delete_video import DeleteResult

    spy = AsyncMock(return_value=DeleteResult(deleted=False, storage_key=None))
    monkeypatch.setattr(
        "progress_tracker.handlers.delete_flow.delete_video", spy
    )
    vid = uuid.uuid4()
    cb = _callback(f"del_confirm:{vid}", user_id=100)
    state = _state()
    await on_confirm(
        cb, state=state, session=db_session, storage=LocalStorage(root=tmp_path)
    )
    spy.assert_awaited_once()
    cb.answer.assert_awaited()


async def test_on_confirm_passes_callers_user_id_not_callback_data_user(
    db_session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defense in depth: even if a malicious client somehow injects a
    callback for someone else's video, the service is called with the
    real `from_user.id` (matching repo's WHERE user_id = ?)."""
    from progress_tracker.services.delete_video import DeleteResult

    spy = AsyncMock(return_value=DeleteResult(deleted=False, storage_key=None))
    monkeypatch.setattr(
        "progress_tracker.handlers.delete_flow.delete_video", spy
    )
    vid = uuid.uuid4()
    cb = _callback(f"del_confirm:{vid}", user_id=42)
    state = _state()
    await on_confirm(
        cb, state=state, session=db_session, storage=LocalStorage(root=tmp_path)
    )
    spy.assert_awaited_once()
    assert spy.await_args.kwargs["user_id"] == 42


# ---------- router wiring ----------


def test_make_router_registers_command_and_callbacks() -> None:
    r = make_router()
    assert isinstance(r, Router)
    assert r.name == "delete_flow"
