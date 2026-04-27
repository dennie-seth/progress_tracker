"""Tests for progress_tracker.handlers.build_root_router."""

from __future__ import annotations

from aiogram import Router

from progress_tracker.handlers import build_root_router


def test_returns_router_named_root() -> None:
    router = build_root_router()
    assert isinstance(router, Router)
    assert router.name == "root"


def test_includes_start_sub_router() -> None:
    router = build_root_router()
    names = [sub.name for sub in router.sub_routers]
    assert "start" in names


def test_includes_delete_flow_sub_router() -> None:
    router = build_root_router()
    names = [sub.name for sub in router.sub_routers]
    assert "delete_flow" in names


def test_each_call_returns_fresh_instance() -> None:
    """build_root_router should not leak state across calls."""
    a = build_root_router()
    b = build_root_router()
    assert a is not b
