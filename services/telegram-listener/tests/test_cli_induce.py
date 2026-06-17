"""Tests for 'tg-listener parser induce' CLI subcommand.

Requires Postgres — skipped when DATABASE_URL is unset (same pattern as
test_sample_collector.py).
"""

from __future__ import annotations

import hashlib
import os
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tg_listener.cli.__main__ import main
from tg_listener.db.models import Channel, Parser, ParserSample

# Skip entire module when Postgres is unavailable.
if not os.environ.get("DATABASE_URL"):
    pytest.skip("DATABASE_URL not set — skipping CLI DB tests", allow_module_level=True)

CHAN_ID = -100999888777

# Minimal valid RegexTable dict accepted by StubSynthProvider.
_STUB_TABLE: dict[str, Any] = {
    "side": {
        "pattern": r"(LONG|SHORT)\s+(\w+)",
        "flags": ["IGNORECASE", "UNICODE"],
        "group": 1,
    },
    "side_map": {"long": "LONG", "short": "SHORT"},
    "symbol": None,
    "symbol_from_side_group": 2,
    "entry": {
        "pattern": r"Entry\s*([\d,.]+)",
        "flags": ["IGNORECASE", "UNICODE"],
        "group": 1,
    },
    "entry_zone": None,
    "sl": {
        "pattern": r"SL\s*([\d,.]+)",
        "flags": ["IGNORECASE", "UNICODE"],
        "group": 1,
    },
    "tp": {
        "pattern": r"TP\s*([\d,.]+)",
        "flags": ["IGNORECASE", "UNICODE"],
        "group": 1,
    },
    "tp_split": None,
    "tp_comma_list": None,
    "leverage": None,
    "pre_clean": None,
    "skip_symbols": [],
}


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _make_sample_text(i: int) -> str:
    return f"LONG XAUUSD Entry 2350 SL 2342 TP 2360 (sample {i})"


async def _seed_channel_and_samples(
    factory: async_sessionmaker[AsyncSession],
    n: int = 10,
) -> None:
    """Insert a Channel + n ParserSamples needed for the induce command."""
    async with factory() as session:
        async with session.begin():
            # Insert channel.
            session.add(Channel(id=CHAN_ID, name="test_induce_chan", auto_approve=False))
            await session.flush()

            for i in range(n):
                text = _make_sample_text(i)
                session.add(
                    ParserSample(
                        channel_id=CHAN_ID,
                        text_hash=_sha256(text),
                        text=text,
                        parsed_by="tier3_llm",
                        parsed_signal={
                            "symbol": "XAUUSD",
                            "side": "LONG",
                            "entry": 2350.0,
                            "sl": 2342.0,
                            "tp": [2360.0],
                            "leverage": None,
                            "confidence": 0.95,
                        },
                        confidence=0.95,
                    )
                )


# ── Tests ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_induce_dry_run_exits_zero_no_db_write(
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--dry-run exits 0 and does NOT create a Parser row."""
    monkeypatch.setenv("INDUCTION_PROVIDER", "stub")

    await _seed_channel_and_samples(db_session_factory, n=10)

    # Patch _make_session_factory to return the test factory.
    import tg_listener.cli.parser_cmd as cmd_module

    monkeypatch.setattr(cmd_module, "_make_session_factory", lambda: db_session_factory)

    rc = main(["parser", "induce", "--channel-id", str(CHAN_ID), "--dry-run"])

    assert rc == 0

    # Verify no Parser row was created.
    async with db_session_factory() as session:
        result = await session.execute(
            select(Parser).where(Parser.channel_id == CHAN_ID)
        )
        parsers = result.scalars().all()

    assert len(parsers) == 0


@pytest.mark.asyncio
async def test_induce_no_dry_run_creates_proposed_parser(
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without --dry-run, a Parser row with status='proposed' is created."""
    monkeypatch.setenv("INDUCTION_PROVIDER", "stub")

    await _seed_channel_and_samples(db_session_factory, n=10)

    import tg_listener.cli.parser_cmd as cmd_module

    monkeypatch.setattr(cmd_module, "_make_session_factory", lambda: db_session_factory)

    # The stub returns a table that parses the sample texts — all should match.
    rc = main(["parser", "induce", "--channel-id", str(CHAN_ID)])

    assert rc == 0

    # Verify a proposed Parser row was created.
    async with db_session_factory() as session:
        result = await session.execute(
            select(Parser).where(Parser.channel_id == CHAN_ID)
        )
        parsers = list(result.scalars().all())

    assert len(parsers) == 1
    assert parsers[0].status == "proposed"
    assert parsers[0].source == "llm_induced"
