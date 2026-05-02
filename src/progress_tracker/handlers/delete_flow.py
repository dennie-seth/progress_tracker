"""/delete FSM — pick a tag, browse most-recent clips, two-tap-delete each.

Flow:
    /delete
      → choose tag (skipped when the user has 0 or 1 tag)
      → bot sends up to 20 most-recent clips for that tag, each as a video
        message with a single `[🗑 Delete]` inline button
      → user taps delete → buttons swap to `[✅ Confirm] [✖ Cancel]`
      → confirm calls `delete_video` (row + on-disk file); the message's
        caption is edited to mark it as deleted
      → cancel reverts the keyboard back to the single delete button

State is held in aiogram's `FSMContext` (MemoryStorage today). The two-tap
confirm step is the safety net — there is no `/trash` or undo window;
deletion is hard, on the user's request.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import cast

import structlog
from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from progress_tracker.db.models import Tag, Video
from progress_tracker.db.repos import TagRepo, VideoRepo
from progress_tracker.services.delete_video import delete_video
from progress_tracker.storage.base import Storage

_log = structlog.get_logger("progress_tracker.delete_flow")


# Cap how many clips we list per tag. Newest first; the "I uploaded a clip
# by accident" flow only ever cares about recent items, and 20 video
# messages is already a wall in the chat.
_LISTING_LIMIT = 20


class DeleteStates(StatesGroup):
    waiting_tag = State()
    browsing = State()


# ---------- keyboards ----------


def _tag_keyboard(tags: Sequence[Tag]) -> InlineKeyboardMarkup:
    """Two columns of tag buttons (callback `del_tag:<id>`).

    Tags are expected pre-sorted by the caller for stable layout across
    repeated `/delete` invocations.
    """
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for t in tags:
        row.append(
            InlineKeyboardButton(text=f"#{t.name}", callback_data=f"del_tag:{t.id}")
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _delete_keyboard(video_id: uuid.UUID) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗑 Delete", callback_data=f"del:{video_id}"
                )
            ]
        ]
    )


def _confirm_keyboard(video_id: uuid.UUID) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Confirm", callback_data=f"del_confirm:{video_id}"
                ),
                InlineKeyboardButton(
                    text="✖ Cancel", callback_data=f"del_cancel:{video_id}"
                ),
            ]
        ]
    )


# ---------- helpers ----------


def _message(call: CallbackQuery) -> Message:
    """Narrow `call.message` to a real `Message` for mypy.

    aiogram types it as `Message | InaccessibleMessage | None`, but every
    callback we register here is dispatched on a message *we just sent in
    this dialog*, so the inaccessible / None branches are unreachable.
    Use `cast` rather than `isinstance` so unit-test mocks (`AsyncMock`)
    pass through unchanged.
    """
    if call.message is None:
        raise RuntimeError("delete_flow callback on inaccessible message")
    return cast(Message, call.message)


def _format_caption(video: Video, tag_name: str) -> str:
    """Caption shown above each delete-button video in the listing.

    Just enough to disambiguate clips at a glance: upload date + the tag.
    """
    return f"#{tag_name} · {video.created_at.strftime('%Y-%m-%d')}"


async def _send_listing(
    bot,
    *,
    chat_id: int,
    videos: Sequence[Video],
    tag_name: str,
    storage: Storage,
) -> None:
    """Send each clip as a video message with a single delete button.

    Happy path: re-send via Telegram's cached `file_id` (no upload, one HTTP
    call per clip). Fallback: when `telegram_file_id` is empty (post-recovery
    rows have a placeholder, since the cached file_id was lost with the DB),
    upload from the local copy via `FSInputFile`. Slower but keeps the
    delete UI working for recovered videos.
    """
    for v in videos:
        caption = _format_caption(v, tag_name)
        keyboard = _delete_keyboard(v.id)
        if v.telegram_file_id:
            await bot.send_video(
                chat_id=chat_id,
                video=v.telegram_file_id,
                caption=caption,
                reply_markup=keyboard,
            )
        else:
            # Storage.open is an async context manager because S3-backed
            # opens stream into a tempfile that's cleaned up on exit. The
            # send_video call must happen inside the with-block so the
            # tempfile survives until aiogram has streamed it to Telegram.
            async with storage.open(v.storage_key) as path:
                await bot.send_video(
                    chat_id=chat_id,
                    video=FSInputFile(path),
                    caption=caption,
                    reply_markup=keyboard,
                )


# ---------- handlers ----------


async def on_delete(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    storage: Storage,
) -> None:
    """Entry point. Lists the user's tags, auto-skips when there's only one."""
    user_id = message.from_user.id
    tags = await TagRepo(session).list_for_user(user_id)
    if not tags:
        await message.answer(
            "You have no tagged videos yet — nothing to delete."
        )
        await state.clear()
        return
    if len(tags) == 1:
        only = tags[0]
        await state.update_data(tag_id=only.id, tag_name=only.name)
        await _open_listing(
            message.bot,
            chat_id=message.chat.id,
            session=session,
            storage=storage,
            user_id=user_id,
            tag=only,
            answer=message.answer,
            state=state,
        )
        return
    await message.answer(
        "Which tag's videos do you want to delete from?",
        reply_markup=_tag_keyboard(tags),
    )
    await state.set_state(DeleteStates.waiting_tag)


async def on_tag_selected(
    call: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    storage: Storage,
) -> None:
    tag_id = int(call.data.split(":", 1)[1])
    tag = await TagRepo(session).get(tag_id)
    if tag is None or tag.user_id != call.from_user.id:
        await call.answer("Unknown tag.", show_alert=True)
        return
    msg = _message(call)
    await state.update_data(tag_id=tag.id, tag_name=tag.name)
    await _open_listing(
        call.bot,
        chat_id=msg.chat.id,
        session=session,
        storage=storage,
        user_id=call.from_user.id,
        tag=tag,
        answer=msg.answer,
        state=state,
    )
    await call.answer()


async def _open_listing(
    bot,
    *,
    chat_id: int,
    session: AsyncSession,
    storage: Storage,
    user_id: int,
    tag: Tag,
    answer,
    state: FSMContext,
) -> None:
    """Common listing path used by `/delete` (one-tag fast path) and
    `on_tag_selected`. Loads videos newest-first up to `_LISTING_LIMIT`,
    sends them with delete buttons, and either advances state to
    `browsing` or clears it for the empty-tag case."""
    videos = await VideoRepo(session).list_for_user_tag(user_id=user_id, tag_id=tag.id)
    if not videos:
        await answer(f"No clips tagged #{tag.name} to delete.")
        await state.clear()
        return
    # list_for_user_tag returns oldest-first; reverse for the delete UX
    # (most-recent-first is what the "I uploaded by mistake" use case wants).
    newest_first = list(reversed(videos))[:_LISTING_LIMIT]
    await answer(
        f"Tap 🗑 next to a clip to delete it. Showing the latest "
        f"{len(newest_first)} clip(s) tagged #{tag.name}."
    )
    await _send_listing(
        bot,
        chat_id=chat_id,
        videos=newest_first,
        tag_name=tag.name,
        storage=storage,
    )
    await state.set_state(DeleteStates.browsing)


async def on_delete_button(call: CallbackQuery, state: FSMContext) -> None:
    """User tapped 🗑 — swap the single button for Confirm/Cancel."""
    video_id = uuid.UUID(call.data.split(":", 1)[1])
    await _message(call).edit_reply_markup(reply_markup=_confirm_keyboard(video_id))
    await call.answer()


async def on_cancel_confirm(call: CallbackQuery, state: FSMContext) -> None:
    """User backed out — restore the original delete button."""
    video_id = uuid.UUID(call.data.split(":", 1)[1])
    await _message(call).edit_reply_markup(reply_markup=_delete_keyboard(video_id))
    await call.answer("Cancelled.")


async def on_confirm(
    call: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    storage: Storage,
) -> None:
    """User tapped ✅ Confirm — delete the row and the on-disk file."""
    video_id = uuid.UUID(call.data.split(":", 1)[1])
    user_id = call.from_user.id  # NEVER trust callback_data for ownership

    result = await delete_video(
        session=session,
        storage=storage,
        user_id=user_id,
        video_id=video_id,
    )

    msg = _message(call)
    if not result.deleted:
        # Either the video doesn't exist (already deleted in another
        # session) or belongs to a different user. Same friendly message
        # in both cases — we don't leak ownership.
        await msg.edit_reply_markup(reply_markup=None)
        try:
            await msg.edit_caption(
                caption=(msg.caption or "")
                + "\n\n⚠️ Couldn't delete (already gone?)."
            )
        except Exception:
            # edit_caption is video-message specific; if the underlying
            # message has no caption (theoretical), fall back to a reply.
            await msg.reply("Couldn't delete (already gone?).")
        await call.answer()
        return

    _log.info(
        "video deleted",
        user_id=user_id,
        video_id=str(video_id),
        storage_key=result.storage_key,
    )
    # Remove the inline keyboard so the row can't be tapped again, and
    # mark the caption to make the deletion visible.
    await msg.edit_reply_markup(reply_markup=None)
    try:
        await msg.edit_caption(
            caption=(msg.caption or "") + "\n\n🗑 Deleted."
        )
    except Exception:
        # Defensive: if the message has no caption for some reason, drop a
        # reply instead. Shouldn't happen for video messages we sent.
        await msg.reply("🗑 Deleted.")
    await call.answer("Deleted.")


def make_router() -> Router:
    """Return a fresh Router wiring the /delete FSM."""
    router = Router(name="delete_flow")

    router.message.register(on_delete, Command("delete"))

    router.callback_query.register(
        on_tag_selected,
        F.data.startswith("del_tag:"),
        StateFilter(DeleteStates.waiting_tag),
    )
    router.callback_query.register(
        on_delete_button,
        F.data.startswith("del:"),
        StateFilter(DeleteStates.browsing),
    )
    router.callback_query.register(
        on_confirm,
        F.data.startswith("del_confirm:"),
        StateFilter(DeleteStates.browsing),
    )
    router.callback_query.register(
        on_cancel_confirm,
        F.data.startswith("del_cancel:"),
        StateFilter(DeleteStates.browsing),
    )

    return router
