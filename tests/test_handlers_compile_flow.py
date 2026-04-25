"""Tests for the compile-flow FSM.

Strategy: handlers are exercised directly with mocked Message / CallbackQuery /
FSMContext; we assert state transitions and reply content. Repo-level
integration is covered in `test_db_repos.py`.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Router
from aiogram.types import InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from progress_tracker.db.repos import TagRepo, UserRepo, VideoRepo
from progress_tracker.handlers.compile_flow import (
    CompileStates,
    DateRange,
    _confirm_keyboard,
    _duration_keyboard,
    _overlay_keyboard,
    _range_keyboard,
    _resolve_since,
    _tag_keyboard,
    make_router,
    on_compile,
    on_confirm,
    on_duration_selected,
    on_overlay_selected,
    on_range_selected,
    on_tag_selected,
)


# ---------- pure helpers ----------


def test_resolve_since_all_returns_none() -> None:
    assert _resolve_since(DateRange.ALL.value) is None


def test_resolve_since_6m_is_about_180_days_ago() -> None:
    cutoff = _resolve_since(DateRange.LAST_6M.value)
    assert cutoff is not None
    age = datetime.now(timezone.utc) - cutoff
    assert timedelta(days=179) <= age <= timedelta(days=181)


def test_resolve_since_1m_is_about_30_days_ago() -> None:
    cutoff = _resolve_since(DateRange.LAST_1M.value)
    assert cutoff is not None
    age = datetime.now(timezone.utc) - cutoff
    assert timedelta(days=29) <= age <= timedelta(days=31)


def test_resolve_since_unknown_returns_none() -> None:
    assert _resolve_since("garbage") is None


# ---------- keyboard structure ----------


def test_tag_keyboard_has_one_button_per_tag() -> None:
    tags = [SimpleNamespace(id=i, name=f"tag{i}") for i in range(5)]
    kb = _tag_keyboard(tags)
    assert isinstance(kb, InlineKeyboardMarkup)
    flat = [b for row in kb.inline_keyboard for b in row]
    assert len(flat) == 5
    assert {b.callback_data for b in flat} == {f"tag:{i}" for i in range(5)}
    assert all(b.text.startswith("#") for b in flat)


def test_range_keyboard_has_three_choices() -> None:
    kb = _range_keyboard()
    flat = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert set(flat) == {"range:all", "range:6m", "range:1m"}


def test_duration_keyboard_has_three_presets() -> None:
    kb = _duration_keyboard()
    flat = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert set(flat) == {"dur:10", "dur:15", "dur:30"}


def test_overlay_keyboard_has_yes_no() -> None:
    kb = _overlay_keyboard()
    flat = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert set(flat) == {"overlay:yes", "overlay:no"}


def test_confirm_keyboard_has_go_and_cancel() -> None:
    kb = _confirm_keyboard()
    flat = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert set(flat) == {"confirm:go", "confirm:cancel"}


# ---------- /compile entry ----------


def _msg(*, user_id: int = 100) -> AsyncMock:
    msg = AsyncMock()
    msg.from_user = SimpleNamespace(id=user_id, username="u", first_name="U")
    return msg


def _state() -> AsyncMock:
    s = AsyncMock()
    s.get_data = AsyncMock(return_value={})
    return s


async def test_on_compile_with_no_tags_replies_and_clears(
    db_session: AsyncSession,
) -> None:
    msg = _msg(user_id=100)
    state = _state()
    await on_compile(msg, state=state, session=db_session)
    msg.answer.assert_awaited_once()
    state.clear.assert_awaited()
    state.set_state.assert_not_awaited()


async def test_on_compile_with_one_tag_auto_skips_to_range(
    db_session: AsyncSession,
) -> None:
    await UserRepo(db_session).upsert(user_id=100, username="u", first_name="U")
    [tag] = await TagRepo(db_session).upsert_many(user_id=100, names=["squat"])
    await db_session.commit()

    msg = _msg(user_id=100)
    state = _state()
    await on_compile(msg, state=state, session=db_session)

    state.update_data.assert_awaited_once_with(tag_id=tag.id, tag_name="squat")
    state.set_state.assert_awaited_once_with(CompileStates.waiting_range)
    msg.answer.assert_awaited_once()
    text = msg.answer.await_args.args[0]
    assert "#squat" in text


async def test_on_compile_with_multiple_tags_asks_for_choice(
    db_session: AsyncSession,
) -> None:
    await UserRepo(db_session).upsert(user_id=100, username="u", first_name="U")
    await TagRepo(db_session).upsert_many(user_id=100, names=["squat", "pr"])
    await db_session.commit()

    msg = _msg(user_id=100)
    state = _state()
    await on_compile(msg, state=state, session=db_session)

    state.set_state.assert_awaited_once_with(CompileStates.waiting_tag)
    msg.answer.assert_awaited_once()
    state.update_data.assert_not_awaited()


# ---------- callback transitions ----------


def _callback(data: str, *, user_id: int = 100) -> AsyncMock:
    cb = AsyncMock()
    cb.from_user = SimpleNamespace(id=user_id, username="u", first_name="U")
    cb.data = data
    cb.message = AsyncMock()
    return cb


async def test_tag_selected_advances_to_range(db_session: AsyncSession) -> None:
    await UserRepo(db_session).upsert(user_id=100, username="u", first_name="U")
    [tag] = await TagRepo(db_session).upsert_many(user_id=100, names=["squat"])
    await db_session.commit()

    cb = _callback(f"tag:{tag.id}", user_id=100)
    state = _state()
    await on_tag_selected(cb, state=state, session=db_session)

    state.update_data.assert_awaited_once_with(tag_id=tag.id, tag_name="squat")
    state.set_state.assert_awaited_once_with(CompileStates.waiting_range)
    cb.answer.assert_awaited()


async def test_tag_selected_rejects_other_users_tag(db_session: AsyncSession) -> None:
    await UserRepo(db_session).upsert(user_id=100, username="u1", first_name="U1")
    await UserRepo(db_session).upsert(user_id=200, username="u2", first_name="U2")
    [other_tag] = await TagRepo(db_session).upsert_many(user_id=200, names=["secret"])
    await db_session.commit()

    cb = _callback(f"tag:{other_tag.id}", user_id=100)
    state = _state()
    await on_tag_selected(cb, state=state, session=db_session)

    # Refused — no state change
    state.update_data.assert_not_awaited()
    state.set_state.assert_not_awaited()


async def test_range_selected_advances_to_duration() -> None:
    cb = _callback("range:6m")
    state = _state()
    await on_range_selected(cb, state=state)
    state.update_data.assert_awaited_once_with(date_range="6m")
    state.set_state.assert_awaited_once_with(CompileStates.waiting_duration)


async def test_duration_selected_advances_to_overlay() -> None:
    cb = _callback("dur:30")
    state = _state()
    await on_duration_selected(cb, state=state)
    state.update_data.assert_awaited_once_with(duration=30)
    state.set_state.assert_awaited_once_with(CompileStates.waiting_overlay)


async def test_overlay_selected_advances_to_confirming_with_summary() -> None:
    cb = _callback("overlay:yes")
    state = _state()
    state.get_data = AsyncMock(
        return_value={"tag_id": 1, "tag_name": "squat", "date_range": "6m", "duration": 30}
    )
    await on_overlay_selected(cb, state=state)

    state.update_data.assert_awaited_once_with(overlay=True)
    state.set_state.assert_awaited_once_with(CompileStates.confirming)
    text = cb.message.edit_text.await_args.args[0]
    assert "#squat" in text
    assert "30" in text


# ---------- confirm + cancel ----------


async def test_confirm_cancel_clears_state(db_session: AsyncSession) -> None:
    cb = _callback("confirm:cancel")
    state = _state()
    await on_confirm(
        cb,
        state=state,
        session=db_session,
        storage=MagicMock(),
        session_factory=MagicMock(),
    )
    state.clear.assert_awaited()


async def test_confirm_go_with_no_candidates_replies_and_clears(
    db_session: AsyncSession,
) -> None:
    """No matching clips -> short-circuit to error reply, no background task."""
    await UserRepo(db_session).upsert(user_id=100, username="u", first_name="U")
    [tag] = await TagRepo(db_session).upsert_many(user_id=100, names=["squat"])
    await db_session.commit()

    cb = _callback("confirm:go", user_id=100)
    state = _state()
    state.get_data = AsyncMock(
        return_value={
            "tag_id": tag.id,
            "tag_name": "squat",
            "date_range": DateRange.ALL.value,
            "duration": 30,
            "overlay": False,
        }
    )
    await on_confirm(
        cb,
        state=state,
        session=db_session,
        storage=MagicMock(),
        session_factory=MagicMock(),
    )

    state.clear.assert_awaited()
    text = cb.message.edit_text.await_args.args[0]
    assert "No clips" in text


async def test_confirm_go_with_candidates_schedules_background_compile(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hands off to the compiler service via asyncio.create_task. We patch
    `compile_progress_reel` so the test doesn't actually run ffmpeg."""
    await UserRepo(db_session).upsert(user_id=100, username="u", first_name="U")
    [tag] = await TagRepo(db_session).upsert_many(user_id=100, names=["squat"])
    repo = VideoRepo(db_session)
    for _ in range(2):
        await repo.create(
            id=uuid.uuid4(), user_id=100, telegram_file_id="x",
            storage_key="x", duration_sec=Decimal("1"), tag_ids=[tag.id],
        )
    await db_session.commit()

    spy = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "progress_tracker.handlers.compile_flow.compile_progress_reel", spy
    )

    cb = _callback("confirm:go", user_id=100)
    cb.message.chat = SimpleNamespace(id=999)
    cb.message.message_id = 7
    cb.bot = AsyncMock()
    state = _state()
    state.get_data = AsyncMock(
        return_value={
            "tag_id": tag.id,
            "tag_name": "squat",
            "date_range": DateRange.ALL.value,
            "duration": 30,
            "overlay": False,
        }
    )

    await on_confirm(
        cb,
        state=state,
        session=db_session,
        storage=MagicMock(),
        session_factory=MagicMock(),
    )

    # Status message edited synchronously…
    state.clear.assert_awaited()
    text = cb.message.edit_text.await_args.args[0]
    assert "Compiling" in text or "compiling" in text.lower()

    # …and the actual compile runs in the background. Yield until the task
    # gets a chance to dispatch the call.
    import asyncio

    for _ in range(5):
        await asyncio.sleep(0)
        if spy.await_count:
            break
    spy.assert_awaited_once()


# ---------- router wiring ----------


def test_make_router_returns_named_router() -> None:
    r = make_router()
    assert isinstance(r, Router)
    assert r.name == "compile_flow"
