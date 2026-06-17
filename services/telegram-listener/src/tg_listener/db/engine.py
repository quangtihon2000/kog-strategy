"""Async SQLAlchemy engine + session factory.

Usage:
    async with get_session() as session:
        result = await session.execute(...)
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tg_listener.db.settings import DBSettings


def _make_engine(settings: DBSettings | None = None):  # type: ignore[no-untyped-def]
    cfg = settings or DBSettings()
    return create_async_engine(cfg.url, echo=cfg.echo, future=True)


def _make_session_factory(
    settings: DBSettings | None = None,
) -> async_sessionmaker[AsyncSession]:
    engine = _make_engine(settings)
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


# Module-level defaults — replace in tests by calling _make_session_factory()
# with a test DBSettings instance.
_default_factory: async_sessionmaker[AsyncSession] | None = None


def get_session_factory(settings: DBSettings | None = None) -> async_sessionmaker[AsyncSession]:
    """Return (or lazily create) the default session factory."""
    global _default_factory
    if _default_factory is None or settings is not None:
        _default_factory = _make_session_factory(settings)
    return _default_factory


@asynccontextmanager
async def get_session(
    factory: async_sessionmaker[AsyncSession] | None = None,
) -> AsyncGenerator[AsyncSession, None]:
    """Context manager that yields an AsyncSession and commits on success."""
    sf = factory or get_session_factory()
    async with sf() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
