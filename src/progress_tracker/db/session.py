"""Async engine + session factory.

`create_engine` and `create_session_factory` are kept as two separate factories
so callers can own engine shutdown (`await engine.dispose()`) independently of
the sessionmaker that produced their sessions.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine(database_url: str, *, echo: bool = False) -> AsyncEngine:
    """Build an async SQLAlchemy engine.

    `pool_pre_ping` keeps long-lived bot connections from dying silently when
    Postgres drops idle TCP sockets.
    """
    return create_async_engine(database_url, echo=echo, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Build an async sessionmaker bound to the given engine.

    `expire_on_commit=False` so loaded ORM attributes remain usable after the
    transaction commits — the common shape for bot handler flows.
    """
    return async_sessionmaker(engine, expire_on_commit=False)
