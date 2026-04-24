"""Tests for the /start and /help handlers."""

from __future__ import annotations

from unittest.mock import AsyncMock

from progress_tracker.handlers.start import WELCOME, on_help, on_start


async def test_on_start_sends_welcome_as_html() -> None:
    msg = AsyncMock()
    await on_start(msg)
    msg.answer.assert_awaited_once_with(WELCOME, parse_mode="HTML")


async def test_on_help_sends_welcome_as_html() -> None:
    msg = AsyncMock()
    await on_help(msg)
    msg.answer.assert_awaited_once_with(WELCOME, parse_mode="HTML")


def test_welcome_mentions_hashtags() -> None:
    """The welcome text must teach the user about hashtag captions."""
    assert "#" in WELCOME


def test_welcome_mentions_commands() -> None:
    assert "/start" in WELCOME
    assert "/help" in WELCOME
