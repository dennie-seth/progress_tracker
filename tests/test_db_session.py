"""Tests for progress_tracker.db.session factories."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from progress_tracker.db.session import create_engine, create_session_factory


def test_create_engine_returns_async_engine() -> None:
    engine = create_engine("postgresql+asyncpg://u:p@h/db")
    assert isinstance(engine, AsyncEngine)


def test_create_session_factory_returns_async_sessionmaker() -> None:
    engine = create_engine("postgresql+asyncpg://u:p@h/db")
    factory = create_session_factory(engine)
    assert isinstance(factory, async_sessionmaker)


def test_create_session_factory_produces_async_sessions() -> None:
    engine = create_engine("postgresql+asyncpg://u:p@h/db")
    factory = create_session_factory(engine)
    session = factory()
    try:
        assert isinstance(session, AsyncSession)
    finally:
        # sync close is fine because the session hasn't been opened
        pass


async def test_session_can_execute_select_1(postgres_url: str) -> None:
    """Integration test: factory produces a session that talks to real Postgres."""
    engine = create_engine(postgres_url)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            result = await session.execute(text("SELECT 1"))
            assert result.scalar_one() == 1
    finally:
        await engine.dispose()
