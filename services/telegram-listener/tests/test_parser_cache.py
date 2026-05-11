"""Tests for parsers.cache — prime, get_table, is_empty, refresh_from_db."""

from __future__ import annotations

import pytest

from tg_listener.db.seed_data import (
    CHANNEL_A_ID,
    CHANNEL_A_REGEX_TABLE,
    CHANNEL_B_ID,
    CHANNEL_B_REGEX_TABLE,
)
from tg_listener.parsers import cache
from tg_listener.parsers.regex_table import RegexTable


def setup_function() -> None:
    """Reset cache before each test to avoid inter-test pollution."""
    cache.prime({})


def test_prime_from_seed_loads_both_channels() -> None:
    cache.prime_from_seed()
    assert cache.get_table(CHANNEL_A_ID) is not None
    assert cache.get_table(CHANNEL_B_ID) is not None


def test_get_table_returns_none_for_unknown_channel() -> None:
    cache.prime_from_seed()
    assert cache.get_table(-9999999999) is None


def test_prime_replaces_existing_cache() -> None:
    cache.prime_from_seed()
    assert cache.get_table(CHANNEL_A_ID) is not None
    cache.prime({})
    assert cache.get_table(CHANNEL_A_ID) is None


def test_is_empty_true_when_cache_cleared() -> None:
    cache.prime({})
    assert cache.is_empty() is True


def test_is_empty_false_after_prime_from_seed() -> None:
    cache.prime_from_seed()
    assert cache.is_empty() is False


def test_prime_from_seed_returns_validated_regex_table() -> None:
    cache.prime_from_seed()
    table_a = cache.get_table(CHANNEL_A_ID)
    assert isinstance(table_a, RegexTable)
    table_b = cache.get_table(CHANNEL_B_ID)
    assert isinstance(table_b, RegexTable)


def test_prime_with_manual_table() -> None:
    table = RegexTable.model_validate(CHANNEL_A_REGEX_TABLE)
    cache.prime({-42: table})
    assert cache.get_table(-42) is table
    assert cache.get_table(CHANNEL_A_ID) is None


def test_prime_is_atomic_replacement() -> None:
    """prime() must replace the whole cache, not merge."""
    cache.prime_from_seed()
    assert cache.get_table(CHANNEL_A_ID) is not None
    assert cache.get_table(CHANNEL_B_ID) is not None

    table = RegexTable.model_validate(CHANNEL_B_REGEX_TABLE)
    cache.prime({CHANNEL_B_ID: table})
    assert cache.get_table(CHANNEL_A_ID) is None  # A gone after replacement
    assert cache.get_table(CHANNEL_B_ID) is not None


@pytest.mark.asyncio
async def test_refresh_from_db_loads_active_parsers(db_engine) -> None:  # type: ignore[no-untyped-def]
    """Insert 2 active parsers, call refresh_from_db, verify cache populated."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from tg_listener.db.models import Channel, Parser

    cache.prime({})

    factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)

    # Insert prerequisite channel rows + active parser rows.
    async with factory() as session:
        async with session.begin():
            session.add_all(
                [
                    Channel(channel_id=CHANNEL_A_ID, name="A", auto_approve=False),
                    Channel(channel_id=CHANNEL_B_ID, name="B", auto_approve=False),
                ]
            )
            await session.flush()
            session.add_all(
                [
                    Parser(
                        channel_id=CHANNEL_A_ID,
                        version=1,
                        status="active",
                        regex_table=CHANNEL_A_REGEX_TABLE,
                        source="seed",
                    ),
                    Parser(
                        channel_id=CHANNEL_B_ID,
                        version=1,
                        status="active",
                        regex_table=CHANNEL_B_REGEX_TABLE,
                        source="seed",
                    ),
                ]
            )

    count = await cache.refresh_from_db(factory)
    assert count == 2
    assert cache.get_table(CHANNEL_A_ID) is not None
    assert cache.get_table(CHANNEL_B_ID) is not None
    assert isinstance(cache.get_table(CHANNEL_A_ID), RegexTable)
    assert isinstance(cache.get_table(CHANNEL_B_ID), RegexTable)


@pytest.mark.asyncio
async def test_refresh_from_db_skips_invalid_rows(db_engine) -> None:  # type: ignore[no-untyped-def]
    """A parser row with invalid regex_table is skipped; valid rows still load."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from tg_listener.db.models import Channel, Parser

    cache.prime({})

    factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)

    async with factory() as session:
        async with session.begin():
            session.add(
                Channel(channel_id=CHANNEL_A_ID, name="A", auto_approve=False)
            )
            await session.flush()
            session.add(
                Parser(
                    channel_id=CHANNEL_A_ID,
                    version=1,
                    status="active",
                    # Missing required fields → validation will fail.
                    regex_table={"bad_key": "bad_value"},
                    source="seed",
                )
            )

    count = await cache.refresh_from_db(factory)
    # The invalid row was skipped → 0 tables loaded.
    assert count == 0
    assert cache.get_table(CHANNEL_A_ID) is None


@pytest.mark.asyncio
async def test_refresh_from_db_ignores_non_active_parsers(db_engine) -> None:  # type: ignore[no-untyped-def]
    """Parsers with status != 'active' are not loaded into the cache."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from tg_listener.db.models import Channel, Parser

    cache.prime({})

    factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)

    async with factory() as session:
        async with session.begin():
            session.add(
                Channel(channel_id=CHANNEL_A_ID, name="A", auto_approve=False)
            )
            await session.flush()
            session.add_all(
                [
                    Parser(
                        channel_id=CHANNEL_A_ID,
                        version=1,
                        status="proposed",
                        regex_table=CHANNEL_A_REGEX_TABLE,
                        source="seed",
                    ),
                    Parser(
                        channel_id=CHANNEL_A_ID,
                        version=2,
                        status="retired",
                        regex_table=CHANNEL_A_REGEX_TABLE,
                        source="seed",
                    ),
                ]
            )

    count = await cache.refresh_from_db(factory)
    assert count == 0
    assert cache.get_table(CHANNEL_A_ID) is None
