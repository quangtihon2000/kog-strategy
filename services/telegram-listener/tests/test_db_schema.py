"""Smoke tests for the DB schema — tables, constraints, index invariants.

Requires Postgres (skipped if DATABASE_URL is not set and no local PG).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tg_listener.db.models import Channel, Parser, ParserSample


@pytest.mark.asyncio
async def test_tables_exist(db_session: AsyncSession) -> None:
    """All four tables should be queryable."""
    for table in ("channels", "parsers", "parser_samples", "parser_eval_runs"):
        result = await db_session.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))
        # Just verifying no error; result can be empty.
        _ = result.fetchall()


@pytest.mark.asyncio
async def test_channel_insert(db_session: AsyncSession) -> None:
    channel = Channel(id=-9999999999, name="Test Channel")
    db_session.add(channel)
    await db_session.flush()

    result = await db_session.get(Channel, -9999999999)
    assert result is not None
    assert result.name == "Test Channel"
    assert result.auto_approve is False


@pytest.mark.asyncio
async def test_parser_status_check_constraint(db_session: AsyncSession) -> None:
    """Inserting a parser with invalid status should raise IntegrityError."""
    from sqlalchemy.exc import IntegrityError

    channel = Channel(id=-8888888888, name="Test Ch")
    db_session.add(channel)
    await db_session.flush()

    bad_parser = Parser(
        channel_id=-8888888888,
        version=1,
        status="invalid_status",  # violates CHECK constraint
        regex_table={},
        source="seed",
    )
    db_session.add(bad_parser)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_parser_unique_version_per_channel(db_session: AsyncSession) -> None:
    """Two parsers with the same channel_id + version should raise UniqueViolation."""
    from sqlalchemy.exc import IntegrityError

    channel = Channel(id=-7777777777, name="Test Ch")
    db_session.add(channel)
    await db_session.flush()

    p1 = Parser(
        channel_id=-7777777777, version=1, status="proposed", regex_table={}, source="seed"
    )
    p2 = Parser(
        channel_id=-7777777777, version=1, status="proposed", regex_table={}, source="seed"
    )
    db_session.add(p1)
    await db_session.flush()
    db_session.add(p2)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_one_active_parser_per_channel(db_session: AsyncSession) -> None:
    """Partial unique index: only one active parser per channel."""
    from sqlalchemy.exc import IntegrityError

    channel = Channel(id=-6666666666, name="Test Ch")
    db_session.add(channel)
    await db_session.flush()

    p1 = Parser(
        channel_id=-6666666666, version=1, status="active", regex_table={}, source="seed"
    )
    p2 = Parser(
        channel_id=-6666666666, version=2, status="active", regex_table={}, source="seed"
    )
    db_session.add(p1)
    await db_session.flush()
    db_session.add(p2)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_sample_dedup(db_session: AsyncSession) -> None:
    """UniqueConstraint (channel_id, text_hash) prevents duplicate samples."""
    from sqlalchemy.exc import IntegrityError

    channel = Channel(id=-5555555555, name="Test Ch")
    db_session.add(channel)
    await db_session.flush()

    s1 = ParserSample(
        channel_id=-5555555555,
        text_hash="a" * 64,
        text="hello",
        parsed_by="regex",
        parsed_signal={},
        confidence=1.0,
    )
    s2 = ParserSample(
        channel_id=-5555555555,
        text_hash="a" * 64,  # same hash
        text="hello",
        parsed_by="regex",
        parsed_signal={},
        confidence=1.0,
    )
    db_session.add(s1)
    await db_session.flush()
    db_session.add(s2)
    with pytest.raises(IntegrityError):
        await db_session.flush()
