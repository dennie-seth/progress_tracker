"""Who may talk to this bot, and from which chat.

Two rules, both applied before anything else runs — in particular before
`DependenciesMiddleware` opens a database session, so an update from a
stranger costs nothing but a log line:

1. **The sender is on the allowlist.** `ALLOWED_TELEGRAM_IDS` names it; when
   that is unset the bot falls back to the users it already knows, see
   `resolve_allowed_ids`.
2. **The chat is a 1:1 private chat.** Every handler answers into the chat
   the update came from, and those answers carry the sender's own clips
   (`/delete` sends the videos themselves). A group is the wrong place for
   them, so the bot refuses to work in one.

The middleware sits on `dp.update`, which means `event` is the raw `Update`.
aiogram's own `UserContextMiddleware` runs first and has already put the
sender and the chat into `data` as `event_from_user` / `event_chat`; those
are the canonical source here.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Chat, Message, TelegramObject
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from progress_tracker.db.models import User

_log = structlog.get_logger("progress_tracker.auth")

PRIVATE_REFUSAL = "This bot is private — it only answers the people it was set up for."
GROUP_REFUSAL = (
    "This bot only works in a private chat: it answers with your own clips, and a "
    "group is the wrong place for them. Send me /start in a direct chat instead."
)


class AllowlistMiddleware(BaseMiddleware):
    """Outermost gate on `dp.update`.

    `allowed_ids` of `None` means "no allowlist" — a fresh install with
    nothing configured and no users yet. The private-chat rule still applies
    in that case.
    """

    def __init__(self, allowed_ids: frozenset[int] | None) -> None:
        self._allowed = allowed_ids

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = data.get("event_from_user")
        if tg_user is None:
            # Nothing to authorise (channel post, poll answer). None of our
            # routers serve those anyway.
            return None

        if self._allowed is not None and tg_user.id not in self._allowed:
            _log.warning(
                "update rejected: sender not on the allowlist",
                user_id=tg_user.id,
                username=getattr(tg_user, "username", None),
            )
            await _refuse(event, PRIVATE_REFUSAL)
            return None

        chat: Chat | None = data.get("event_chat")
        if chat is None and isinstance(event, Message):
            chat = event.chat
        if getattr(chat, "type", None) != "private":
            _log.warning(
                "update rejected: not a private chat",
                user_id=tg_user.id,
                chat_type=getattr(chat, "type", None),
            )
            await _refuse(event, GROUP_REFUSAL)
            return None

        return await handler(event, data)


async def resolve_allowed_ids(
    configured: frozenset[int],
    session_factory: async_sessionmaker[AsyncSession],
) -> frozenset[int] | None:
    """Work out the allowlist at startup, and say in the log which rule won.

    `ALLOWED_TELEGRAM_IDS` wins whenever it is set. With nothing configured,
    the users already in the database become the allowlist: that closes an
    existing deployment to strangers without an `.env` edit, which is the
    only reason this function touches the database at all. A fresh install
    has neither, so there is nobody to infer — the bot stays open, as it was
    before this middleware existed, and warns about it on every start.

    Called once, after recovery, before polling starts. The list does not
    change while the bot runs; adding someone means editing `.env` and
    restarting.
    """
    if configured:
        _log.info("allowlist taken from ALLOWED_TELEGRAM_IDS", ids=sorted(configured))
        return configured

    async with session_factory() as session:
        known = frozenset((await session.execute(select(User.id))).scalars().all())

    if known:
        _log.warning(
            "ALLOWED_TELEGRAM_IDS is not set; allowing only the users already in "
            "the database. Pin them in .env so the list cannot drift.",
            ids=sorted(known),
        )
        return known

    _log.warning(
        "ALLOWED_TELEGRAM_IDS is not set and the database has no users yet — "
        "anyone who finds this bot can upload to it. Set ALLOWED_TELEGRAM_IDS "
        "in .env as soon as you know your Telegram id."
    )
    return None


async def _refuse(event: TelegramObject, text: str) -> None:
    """Tell the sender why they were refused, if there is anything to answer.

    Deliberately swallows delivery failures: a blocked bot or a deleted chat
    must not turn a refusal into an exception in the polling loop.
    """
    callback: Any = (
        event if isinstance(event, CallbackQuery) else getattr(event, "callback_query", None)
    )
    message: Any = (
        event
        if isinstance(event, Message)
        else getattr(event, "message", None) or getattr(event, "edited_message", None)
    )
    try:
        if callback is not None:
            await callback.answer(text, show_alert=True)
        elif message is not None:
            await message.answer(text)
    except Exception:
        _log.debug("could not deliver the refusal", exc_info=True)
