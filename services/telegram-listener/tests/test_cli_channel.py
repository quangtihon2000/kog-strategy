"""Tests for 'tg-listener channel set-auto-approve' and _run_induce auto-approve wiring.

Requires Postgres — skipped when DATABASE_URL is unset (same pattern as
test_cli_induce.py).
"""

from __future__ import annotations

import hashlib
import json
import os
from io import StringIO
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tg_listener.cli.__main__ import main
from tg_listener.db.models import Channel, Parser, ParserSample

# Skip entire module when Postgres is unavailable.
if not os.environ.get("DATABASE_URL"):
    pytest.skip("DATABASE_URL not set — skipping CLI DB tests", allow_module_level=True)

CHAN_ID = -100444555666

# Minimal valid RegexTable dict khớp với StubSynthProvider.
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
    channel_id: int = CHAN_ID,
    auto_approve: bool = False,
    n: int = 10,
) -> None:
    """Insert Channel + n ParserSamples."""
    async with factory() as session:
        async with session.begin():
            session.add(
                Channel(id=channel_id, name="test_channel_chan", auto_approve=auto_approve)
            )
            await session.flush()
            for i in range(n):
                text = _make_sample_text(i)
                session.add(
                    ParserSample(
                        channel_id=channel_id,
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


def _capture_stdout_main(argv: list[str]) -> tuple[int, dict]:  # type: ignore[type-arg]
    """Run main(argv) and capture its stdout JSON output."""
    buf = StringIO()
    with patch("sys.stdout", buf):
        rc = main(argv)
    output = buf.getvalue().strip()
    return rc, json.loads(output)


# ── Tests: channel set-auto-approve ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_channel_set_auto_approve_roundtrip(
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """set-auto-approve true then false → DB roundtrip, idempotent on second call."""
    import tg_listener.cli.channel_cmd as ch_module

    monkeypatch.setattr(ch_module, "_make_session_factory", lambda: db_session_factory)

    # Đặt true lần 1.
    rc1, data1 = _capture_stdout_main(
        ["channel", "set-auto-approve", "--channel-id", str(CHAN_ID), "--value", "true"]
    )
    assert rc1 == 0
    assert data1["status"] == "ok"
    assert data1["channel_id"] == CHAN_ID
    assert data1["auto_approve"] is True

    # Kiểm tra DB.
    async with db_session_factory() as session:
        result = await session.execute(select(Channel).where(Channel.id == CHAN_ID))
        ch = result.scalar_one()
        assert ch.auto_approve is True

    # Đặt false (idempotent — gọi lần 2).
    rc2, data2 = _capture_stdout_main(
        ["channel", "set-auto-approve", "--channel-id", str(CHAN_ID), "--value", "false"]
    )
    assert rc2 == 0
    assert data2["auto_approve"] is False

    # Kiểm tra DB sau khi đặt false.
    async with db_session_factory() as session:
        result = await session.execute(select(Channel).where(Channel.id == CHAN_ID))
        ch = result.scalar_one()
        assert ch.auto_approve is False


@pytest.mark.asyncio
async def test_channel_set_auto_approve_creates_row_if_missing(
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """set-auto-approve creates the channel row via upsert even if it doesn't exist."""
    import tg_listener.cli.channel_cmd as ch_module

    monkeypatch.setattr(ch_module, "_make_session_factory", lambda: db_session_factory)

    new_chan_id = -100777888999
    rc, data = _capture_stdout_main(
        [
            "channel",
            "set-auto-approve",
            "--channel-id",
            str(new_chan_id),
            "--value",
            "1",
        ]
    )
    assert rc == 0
    assert data["auto_approve"] is True

    async with db_session_factory() as session:
        result = await session.execute(select(Channel).where(Channel.id == new_chan_id))
        ch = result.scalar_one_or_none()
        assert ch is not None
        assert ch.auto_approve is True


# ── Tests: _run_induce auto-approve wiring ────────────────────────────────────


@pytest.mark.asyncio
async def test_induce_auto_approve_true_activates(
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_run_induce với channel auto_approve=True → status activated, parser active."""
    monkeypatch.setenv("INDUCTION_PROVIDER", "stub")

    # Seed channel với auto_approve=True + samples.
    await _seed_channel_and_samples(db_session_factory, auto_approve=True, n=10)

    import tg_listener.cli.parser_cmd as cmd_module

    monkeypatch.setattr(cmd_module, "_make_session_factory", lambda: db_session_factory)

    rc, data = _capture_stdout_main(
        ["parser", "induce", "--channel-id", str(CHAN_ID)]
    )

    assert rc == 0
    assert data["status"] == "activated"
    assert "parser_id" in data
    assert "version" in data

    # Kiểm tra DB: parser phải ở trạng thái active.
    async with db_session_factory() as session:
        result = await session.execute(
            select(Parser).where(Parser.channel_id == CHAN_ID)
        )
        parsers = list(result.scalars().all())

    assert len(parsers) == 1
    assert parsers[0].status == "active"
    assert parsers[0].id == data["parser_id"]


@pytest.mark.asyncio
async def test_induce_auto_approve_false_stays_proposed(
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_run_induce với channel auto_approve=False → status proposed, parser proposed."""
    monkeypatch.setenv("INDUCTION_PROVIDER", "stub")

    # Seed channel với auto_approve=False (default).
    await _seed_channel_and_samples(db_session_factory, auto_approve=False, n=10)

    import tg_listener.cli.parser_cmd as cmd_module

    monkeypatch.setattr(cmd_module, "_make_session_factory", lambda: db_session_factory)

    rc, data = _capture_stdout_main(
        ["parser", "induce", "--channel-id", str(CHAN_ID)]
    )

    assert rc == 0
    assert data["status"] == "proposed"

    # Kiểm tra DB.
    async with db_session_factory() as session:
        result = await session.execute(
            select(Parser).where(Parser.channel_id == CHAN_ID)
        )
        parsers = list(result.scalars().all())

    assert len(parsers) == 1
    assert parsers[0].status == "proposed"
