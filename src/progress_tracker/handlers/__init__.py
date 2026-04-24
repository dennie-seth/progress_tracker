"""aiogram routers are collected from this package."""

from aiogram import Router

from progress_tracker.handlers import start

__all__ = ["build_root_router"]


def build_root_router() -> Router:
    """Aggregate every feature router into a single root router.

    Later milestones append their routers here (video upload, compile FSM, ...).
    Each call returns a fresh Router tree so the factory can be invoked
    repeatedly (e.g. from tests).
    """
    root = Router(name="root")
    root.include_router(start.make_router())
    return root
