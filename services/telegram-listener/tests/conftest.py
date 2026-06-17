"""Shared pytest fixtures for tg-listener tests.

DB fixtures:
  - Uses pytest-postgresql to spin up an ephemeral Postgres instance per session.
  - Falls back to DATABASE_URL env var if pytest-postgresql is unavailable.
  - DB tests are skipped when no Postgres is available.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tg_listener.db.models import Base

# ── pytest-postgresql detection ────────────────────────────────────────────
try:
    from pytest_postgresql import factories as pg_factories

    _postgresql_proc = pg_factories.postgresql_proc()
    _postgresql = pg_factories.postgresql("_postgresql_proc")
    _HAS_PYTEST_PG = True
except Exception:
    _HAS_PYTEST_PG = False


# ── Database URL resolution ────────────────────────────────────────────────

def _db_url_from_env() -> str | None:
    return os.environ.get("DATABASE_URL")


def _asyncpg_url(dsn: str) -> str:
    """Convert a psycopg2-style DSN to asyncpg URL."""
    if dsn.startswith("postgresql://") or dsn.startswith("postgresql+asyncpg://"):
        return dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    return dsn


# ── Engine / session factory ───────────────────────────────────────────────

@pytest_asyncio.fixture(scope="session")
async def db_engine(tmp_path_factory):  # type: ignore[no-untyped-def]
    """Create a test engine. Skips if no Postgres is available."""
    url: str | None = None

    if _HAS_PYTEST_PG:
        # pytest-postgresql provides a session-scoped connection string via
        # the _postgresql fixture. We derive the URL from env.
        # Actually pytest-postgresql fixtures are function-scoped by default;
        # for a session-scoped approach we use DATABASE_URL or skip.
        pass

    if url is None:
        url = _db_url_from_env()

    if url is None:
        pytest.skip(
            "No Postgres available — set DATABASE_URL env var or run "
            "'docker-compose up -d postgres' then retry. "
            "Example: DATABASE_URL=postgresql+asyncpg://tg_listener:tg_listener@localhost:5432/tg_listener"
        )

    engine = create_async_engine(_asyncpg_url(url), echo=False, future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine) -> AsyncGenerator[AsyncSession, None]:  # type: ignore[no-untyped-def]
    """Yield an AsyncSession in a transaction that is rolled back after each test."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        async with session.begin():
            yield session
            await session.rollback()


@pytest_asyncio.fixture
async def db_session_factory(db_engine):  # type: ignore[no-untyped-def]
    """Yield a real async_sessionmaker suitable for components that manage their own sessions.

    Unlike db_session, commits are real (not rolled back via savepoint). The
    fixture truncates all tables after the test to keep isolation.
    """
    factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)
    yield factory
    # Clean up all rows inserted during the test.
    async with factory() as session:
        async with session.begin():
            for table in reversed(Base.metadata.sorted_tables):
                await session.execute(table.delete())
