"""Channel-id → RegexTable cache. Loaded from DB Parser rows (status='active').

Tests prime the cache directly via `prime_from_seed()`. Production code calls
`refresh_from_db(session_factory)` once at startup and then schedules
`start_refresh_task(session_factory, interval_s=60.0)` to keep it fresh.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tg_listener.parsers.regex_table import RegexTable

log = logging.getLogger(__name__)

# Channel-id → compiled RegexTable. Mutated atomically via dict-replacement.
_cache: dict[int, RegexTable] = {}


def get_table(channel_id: int) -> RegexTable | None:
    """Return the cached RegexTable for the given channel_id, or None."""
    return _cache.get(channel_id)


def is_empty() -> bool:
    """Return True if the cache has no entries."""
    return len(_cache) == 0


def prime(tables: dict[int, RegexTable]) -> None:
    """Replace cache wholesale. Used by tests and bootstrap."""
    global _cache
    _cache = dict(tables)


def prime_from_seed() -> None:
    """Bootstrap cache from `db/seed_data.py` constants. No DB needed."""
    from tg_listener.db.seed_data import (
        CHANNEL_A_ID,
        CHANNEL_A_REGEX_TABLE,
        CHANNEL_B_ID,
        CHANNEL_B_REGEX_TABLE,
    )

    prime(
        {
            CHANNEL_A_ID: RegexTable.model_validate(CHANNEL_A_REGEX_TABLE),
            CHANNEL_B_ID: RegexTable.model_validate(CHANNEL_B_REGEX_TABLE),
        }
    )


async def refresh_from_db(
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    """Reload all active parsers from DB. Returns the number of tables loaded.

    Atomic: builds a new dict, then replaces `_cache` in one assignment.
    Errors loading individual rows are logged; the row is skipped, the rest load.
    """
    from sqlalchemy import select

    from tg_listener.db.models import Parser

    new_tables: dict[int, RegexTable] = {}
    async with session_factory() as session:
        result = await session.execute(
            select(Parser).where(Parser.status == "active")
        )
        for row in result.scalars().all():
            try:
                new_tables[row.channel_id] = RegexTable.model_validate(
                    row.regex_table
                )
            except Exception as e:  # validation error — skip this row
                log.warning(
                    "parser_cache_skip_invalid",
                    extra={
                        "channel_id": row.channel_id,
                        "version": row.version,
                        "error": str(e),
                    },
                )
    prime(new_tables)
    log.info("parser_cache_refreshed", extra={"count": len(new_tables)})
    return len(new_tables)


def start_refresh_task(
    session_factory: async_sessionmaker[AsyncSession],
    interval_s: float = 60.0,
) -> asyncio.Task[None]:
    """Schedule periodic `refresh_from_db`. Returns the task handle.

    Caller is responsible for cancelling the task on shutdown.
    """

    async def _loop() -> None:
        while True:
            try:
                await refresh_from_db(session_factory)
            except Exception as e:  # network/DB errors during refresh
                log.warning(
                    "parser_cache_refresh_failed", extra={"error": str(e)}
                )
            await asyncio.sleep(interval_s)

    return asyncio.create_task(_loop(), name="parser_cache_refresh")
