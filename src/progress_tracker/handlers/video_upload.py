"""/video upload handler — entry point for users sending a training clip."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from progress_tracker.services.ingest import ingest_video
from progress_tracker.storage.base import Storage

NO_HASHTAG_HINT = (
    "Please add at least one hashtag to the caption, e.g. <code>#squat day 1</code>."
)


def _format_saved_reply(tag_names: list[str], prior_count: int) -> str:
    tags_str = " ".join(f"#{t}" for t in tag_names)
    if prior_count == 0:
        return (
            f"Saved! {tags_str}\n"
            "This is your first clip with these tags. Once you have a few more, "
            "I can compile a progress reel."
        )
    return (
        f"Saved! {tags_str}\n"
        f"You have {prior_count} prior clip(s) with these tags. "
        "Send me more, or ask for a progress reel once you're ready."
    )


async def on_video(
    message: Message,
    session: AsyncSession,
    storage: Storage,
) -> None:
    """Handle an incoming video message."""
    if not message.caption or "#" not in message.caption:
        await message.reply(NO_HASHTAG_HINT, parse_mode="HTML")
        return

    result = await ingest_video(
        bot=message.bot,
        message=message,
        session=session,
        storage=storage,
    )

    if result is None:
        # Caption had a `#` but no actual hashtag word — same hint.
        await message.reply(NO_HASHTAG_HINT, parse_mode="HTML")
        return

    await message.reply(_format_saved_reply(result.tag_names, result.prior_count))


def make_router() -> Router:
    """Return a fresh Router wiring the video upload handler."""
    router = Router(name="video_upload")
    router.message.register(on_video, F.video)
    return router
