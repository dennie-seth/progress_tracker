"""Tests for AllowlistMiddleware: who may talk to the bot, and from where.

The middleware runs at the `Update` level, so the events here are shaped
like aiogram hands them over: the raw update object plus the `data` dict
aiogram's own `UserContextMiddleware` has already filled with
`event_from_user` / `event_chat`.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from progress_tracker.db.repos import UserRepo
from progress_tracker.db.session import create_session_factory
from progress_tracker.middlewares.auth import (
    GROUP_REFUSAL,
    PRIVATE_REFUSAL,
    AllowlistMiddleware,
    resolve_allowed_ids,
)


def _message_update(
    user_id: int | None = 7, chat_type: str = "private"
) -> tuple[Any, dict[str, Any]]:
    event = SimpleNamespace(
        message=AsyncMock(), edited_message=None, callback_query=None
    )
    data: dict[str, Any] = {
        "event_from_user": SimpleNamespace(id=user_id) if user_id is not None else None,
        "event_chat": SimpleNamespace(type=chat_type),
    }
    return event, data


def _callback_update(
    user_id: int = 7, chat_type: str = "private"
) -> tuple[Any, dict[str, Any]]:
    event = SimpleNamespace(
        message=None, edited_message=None, callback_query=AsyncMock()
    )
    data: dict[str, Any] = {
        "event_from_user": SimpleNamespace(id=user_id),
        "event_chat": SimpleNamespace(type=chat_type),
    }
    return event, data


async def _run(mw: AllowlistMiddleware, event: Any, data: dict[str, Any]) -> bool:
    """Run the middleware; return whether the handler was reached."""
    reached = False

    async def handler(_event: Any, _data: dict[str, Any]) -> str:
        nonlocal reached
        reached = True
        return "ok"

    await mw(handler, event, data)
    return reached


# ---------- the allowlist ----------


async def test_allowed_user_in_private_chat_reaches_the_handler() -> None:
    event, data = _message_update(user_id=7)
    assert await _run(AllowlistMiddleware(frozenset({7})), event, data) is True
    event.message.answer.assert_not_awaited()


async def test_stranger_is_refused_and_told_the_bot_is_private() -> None:
    event, data = _message_update(user_id=999)
    assert await _run(AllowlistMiddleware(frozenset({7})), event, data) is False
    event.message.answer.assert_awaited_once_with(PRIVATE_REFUSAL)


async def test_stranger_pressing_a_button_is_refused_with_an_alert() -> None:
    event, data = _callback_update(user_id=999)
    assert await _run(AllowlistMiddleware(frozenset({7})), event, data) is False
    event.callback_query.answer.assert_awaited_once_with(
        PRIVATE_REFUSAL, show_alert=True
    )


async def test_no_allowlist_lets_anyone_through() -> None:
    """`None` = nothing configured and no known users — the pre-existing
    open behaviour, kept so a fresh install still works out of the box."""
    event, data = _message_update(user_id=999)
    assert await _run(AllowlistMiddleware(None), event, data) is True


async def test_update_without_a_user_is_dropped_silently() -> None:
    event, data = _message_update(user_id=None)
    assert await _run(AllowlistMiddleware(frozenset({7})), event, data) is False
    event.message.answer.assert_not_awaited()


async def test_a_failing_refusal_does_not_break_the_rejection() -> None:
    """A blocked bot / deleted chat must not turn a refusal into a crash."""
    event, data = _message_update(user_id=999)
    event.message.answer.side_effect = RuntimeError("chat not found")
    assert await _run(AllowlistMiddleware(frozenset({7})), event, data) is False


# ---------- the private-chat rule ----------


async def test_group_chat_is_refused_even_for_an_allowed_user() -> None:
    event, data = _message_update(user_id=7, chat_type="supergroup")
    assert await _run(AllowlistMiddleware(frozenset({7})), event, data) is False
    event.message.answer.assert_awaited_once_with(GROUP_REFUSAL)


async def test_group_chat_is_refused_when_no_allowlist_is_configured() -> None:
    event, data = _message_update(user_id=7, chat_type="group")
    assert await _run(AllowlistMiddleware(None), event, data) is False


async def test_event_without_a_chat_is_refused() -> None:
    """Fail closed: an update we can't place in a chat isn't served."""
    event, data = _message_update(user_id=7)
    data["event_chat"] = None
    assert await _run(AllowlistMiddleware(frozenset({7})), event, data) is False


# ---------- resolving the allowlist at startup ----------


async def test_resolve_prefers_the_configured_ids(
    db_engine: AsyncEngine, db_session: AsyncSession
) -> None:
    await UserRepo(db_session).upsert(user_id=1, username=None, first_name=None)
    await db_session.commit()
    factory = create_session_factory(db_engine)

    assert await resolve_allowed_ids(frozenset({42}), factory) == frozenset({42})


async def test_resolve_falls_back_to_users_already_in_the_database(
    db_engine: AsyncEngine, db_session: AsyncSession
) -> None:
    """An existing deployment gets locked down without touching its `.env`."""
    await UserRepo(db_session).upsert(user_id=575, username=None, first_name=None)
    await UserRepo(db_session).upsert(user_id=576, username=None, first_name=None)
    await db_session.commit()
    factory = create_session_factory(db_engine)

    assert await resolve_allowed_ids(frozenset(), factory) == frozenset({575, 576})


async def test_resolve_returns_none_on_a_fresh_install(
    db_engine: AsyncEngine, db_session: AsyncSession
) -> None:
    factory = create_session_factory(db_engine)

    assert await resolve_allowed_ids(frozenset(), factory) is None
