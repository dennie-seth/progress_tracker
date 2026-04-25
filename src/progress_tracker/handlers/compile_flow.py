"""Compile FSM — multi-step dialog for picking compile parameters.

Flow:
    /compile
      → choose tag (skipped when the user has exactly one)
      → choose date range (all / last 6 months / last 1 month)
      → choose target duration (10s / 15s / 30s)
      → overlay date on clips? (yes / no)
      → confirm
      → trigger compile  (stub — milestone 6 wires ffmpeg)

State is held in aiogram's `FSMContext` (MemoryStorage today). Each callback
edits the previous bot message in place to keep the chat tidy.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

import structlog
from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from progress_tracker.db.models import Tag
from progress_tracker.db.repos import TagRepo, VideoRepo

_log = structlog.get_logger("progress_tracker.compile_flow")


class CompileStates(StatesGroup):
    waiting_tag = State()
    waiting_range = State()
    waiting_duration = State()
    waiting_overlay = State()
    confirming = State()


class DateRange(str, Enum):
    ALL = "all"
    LAST_6M = "6m"
    LAST_1M = "1m"


_VALID_DURATIONS = (10, 15, 30)


# ---------- keyboards ----------


def _tag_keyboard(tags: list[Tag]) -> InlineKeyboardMarkup:
    """Two columns of tag buttons. Tags are expected pre-sorted by caller."""
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for t in tags:
        row.append(InlineKeyboardButton(text=f"#{t.name}", callback_data=f"tag:{t.id}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _range_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="All time", callback_data="range:all"),
                InlineKeyboardButton(text="Last 6 months", callback_data="range:6m"),
            ],
            [InlineKeyboardButton(text="Last month", callback_data="range:1m")],
        ]
    )


def _duration_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="10s", callback_data="dur:10"),
                InlineKeyboardButton(text="15s", callback_data="dur:15"),
                InlineKeyboardButton(text="30s", callback_data="dur:30"),
            ]
        ]
    )


def _overlay_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Yes", callback_data="overlay:yes"),
                InlineKeyboardButton(text="No", callback_data="overlay:no"),
            ]
        ]
    )


def _confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Generate", callback_data="confirm:go"),
                InlineKeyboardButton(text="✖ Cancel", callback_data="confirm:cancel"),
            ]
        ]
    )


# ---------- helpers ----------


def _resolve_since(range_choice: str) -> datetime | None:
    """Translate a DateRange choice into a `created_at >= ?` lower bound.

    Returns None for "all time" or any value we don't recognize (defensive —
    unknown choices fall back to all time rather than blocking the user).
    """
    now = datetime.now(timezone.utc)
    if range_choice == DateRange.LAST_6M.value:
        return now - timedelta(days=180)
    if range_choice == DateRange.LAST_1M.value:
        return now - timedelta(days=30)
    return None


def _format_summary(data: dict[str, Any]) -> str:
    overlay_label = "yes" if data.get("overlay") else "no"
    range_labels = {
        DateRange.ALL.value: "all time",
        DateRange.LAST_6M.value: "last 6 months",
        DateRange.LAST_1M.value: "last month",
    }
    range_label = range_labels.get(data.get("date_range", ""), data.get("date_range", "?"))
    return (
        f"Tag: #{data.get('tag_name', '?')}\n"
        f"Range: {range_label}\n"
        f"Duration: {data.get('duration', '?')}s\n"
        f"Overlay date: {overlay_label}"
    )


# ---------- handlers ----------


async def on_compile(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    """Entry point. Lists the user's tags, auto-skips when there's only one."""
    user_id = message.from_user.id
    tags = await TagRepo(session).list_for_user(user_id)
    if not tags:
        await message.answer(
            "You have no tagged videos yet. "
            "Send me one first with a #hashtag in the caption."
        )
        await state.clear()
        return
    if len(tags) == 1:
        only = tags[0]
        await state.update_data(tag_id=only.id, tag_name=only.name)
        await message.answer(
            f"Compiling for #{only.name}.\nSelect a date range:",
            reply_markup=_range_keyboard(),
        )
        await state.set_state(CompileStates.waiting_range)
        return
    await message.answer(
        "Which tag do you want a progress reel for?",
        reply_markup=_tag_keyboard(tags),
    )
    await state.set_state(CompileStates.waiting_tag)


async def on_tag_selected(
    call: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    tag_id = int(call.data.split(":", 1)[1])
    tag = await TagRepo(session).get(tag_id)
    if tag is None or tag.user_id != call.from_user.id:
        await call.answer("Unknown tag.", show_alert=True)
        return
    await state.update_data(tag_id=tag.id, tag_name=tag.name)
    await call.message.edit_text(
        f"Compiling for #{tag.name}.\nSelect a date range:",
        reply_markup=_range_keyboard(),
    )
    await state.set_state(CompileStates.waiting_range)
    await call.answer()


async def on_range_selected(call: CallbackQuery, state: FSMContext) -> None:
    choice = call.data.split(":", 1)[1]
    await state.update_data(date_range=choice)
    await call.message.edit_text(
        "Target duration?",
        reply_markup=_duration_keyboard(),
    )
    await state.set_state(CompileStates.waiting_duration)
    await call.answer()


async def on_duration_selected(call: CallbackQuery, state: FSMContext) -> None:
    duration = int(call.data.split(":", 1)[1])
    if duration not in _VALID_DURATIONS:
        await call.answer("Unsupported duration.", show_alert=True)
        return
    await state.update_data(duration=duration)
    await call.message.edit_text(
        "Overlay the upload date on each clip?",
        reply_markup=_overlay_keyboard(),
    )
    await state.set_state(CompileStates.waiting_overlay)
    await call.answer()


async def on_overlay_selected(call: CallbackQuery, state: FSMContext) -> None:
    overlay = call.data.split(":", 1)[1] == "yes"
    await state.update_data(overlay=overlay)
    data = await state.get_data()
    await call.message.edit_text(
        f"Ready to compile:\n{_format_summary(data)}\n\nProceed?",
        reply_markup=_confirm_keyboard(),
    )
    await state.set_state(CompileStates.confirming)
    await call.answer()


async def on_confirm(
    call: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    choice = call.data.split(":", 1)[1]
    if choice == "cancel":
        await call.message.edit_text("Compilation cancelled.")
        await state.clear()
        await call.answer()
        return

    data = await state.get_data()
    user_id = call.from_user.id
    since = _resolve_since(data.get("date_range", ""))
    candidates = await VideoRepo(session).list_for_user_tag(
        user_id=user_id, tag_id=data["tag_id"], since=since
    )

    _log.info(
        "compile requested",
        user_id=user_id,
        tag_id=data.get("tag_id"),
        date_range=data.get("date_range"),
        duration=data.get("duration"),
        overlay=data.get("overlay"),
        candidate_count=len(candidates),
    )

    if not candidates:
        await call.message.edit_text(
            "No clips matched the selected range. Cancelled."
        )
    else:
        await call.message.edit_text(
            f"Got it — {len(candidates)} matching clip(s) selected.\n"
            "(Compilation engine isn't wired yet — milestone 6.)"
        )
    await state.clear()
    await call.answer()


async def on_cancel(message: Message, state: FSMContext) -> None:
    """`/cancel` from any state — clears FSM data and acknowledges."""
    if await state.get_state() is None:
        await message.answer("Nothing to cancel.")
        return
    await state.clear()
    await message.answer("Cancelled.")


def make_router() -> Router:
    """Return a fresh Router wiring the /compile FSM."""
    router = Router(name="compile_flow")

    router.message.register(on_compile, Command("compile"))
    router.message.register(on_cancel, Command("cancel"))

    router.callback_query.register(
        on_tag_selected,
        F.data.startswith("tag:"),
        StateFilter(CompileStates.waiting_tag),
    )
    router.callback_query.register(
        on_range_selected,
        F.data.startswith("range:"),
        StateFilter(CompileStates.waiting_range),
    )
    router.callback_query.register(
        on_duration_selected,
        F.data.startswith("dur:"),
        StateFilter(CompileStates.waiting_duration),
    )
    router.callback_query.register(
        on_overlay_selected,
        F.data.startswith("overlay:"),
        StateFilter(CompileStates.waiting_overlay),
    )
    router.callback_query.register(
        on_confirm,
        F.data.startswith("confirm:"),
        StateFilter(CompileStates.confirming),
    )

    return router
