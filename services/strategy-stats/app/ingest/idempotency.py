"""Helpers for INSERT ... ON CONFLICT DO NOTHING using SQLAlchemy core."""
from __future__ import annotations

from typing import Any

from sqlalchemy import Table
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession


async def insert_ignore(session: AsyncSession, table: Table, values: dict[str, Any]) -> bool:
    """Insert one row; return True if inserted, False if conflict."""
    stmt = pg_insert(table).values(**values).on_conflict_do_nothing()
    result = await session.execute(stmt)
    return bool(result.rowcount)
