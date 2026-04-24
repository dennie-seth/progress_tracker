"""/start and /help handlers."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

WELCOME = (
    "Hi! I'm your progress tracker bot.\n\n"
    "Send me a short training video with hashtags in the caption, e.g.\n"
    "  <code>#squat day 1</code>\n"
    "I'll save it. Once you have a few clips with the same tag, I can compile "
    "a progress reel (≤30s) showing your journey from first to latest.\n\n"
    "Commands:\n"
    "/start — this message\n"
    "/help — same as /start"
)


async def on_start(message: Message) -> None:
    await message.answer(WELCOME, parse_mode="HTML")


async def on_help(message: Message) -> None:
    await message.answer(WELCOME, parse_mode="HTML")


def make_router() -> Router:
    """Return a fresh Router with /start and /help wired up.

    A factory (rather than a module-level singleton) means the router can be
    rebuilt — important for tests and for any flow that reconstructs the
    dispatcher.
    """
    router = Router(name="start")
    router.message.register(on_start, CommandStart())
    router.message.register(on_help, Command("help"))
    return router
