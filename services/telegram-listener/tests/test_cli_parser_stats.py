"""Tests for 'tg-listener parser stats' CLI subcommand and related repo methods.

Requires Postgres — skipped when DATABASE_URL is unset.
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
from tg_listener.db.models import Channel, Parser, ParserEvalRun, ParserSample

# Skip entire module when Postgres is unavailable.
if not os.environ.get("DATABASE_URL"):
    pytest.skip("DATABASE_URL not set — skipping CLI DB tests", allow_module_level=True)

CHAN_ID = -100444555666

# Minimal valid RegexTable dict (mirrors _STUB_TABLE in test_cli_induce.py).
_TABLE: dict[str, Any] = {
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


async def _seed_channel(
    factory: async_sessionmaker[AsyncSession],
    channel_id: int = CHAN_ID,
    auto_approve: bool = False,
) -> None:
    async with factory() as session:
        async with session.begin():
            from sqlalchemy import select as sa_select

            existing = await session.execute(
                sa_select(Channel).where(Channel.id == channel_id)
            )
            if existing.scalar_one_or_none() is None:
                session.add(
                    Channel(id=channel_id, name="test_stats_chan", auto_approve=auto_approve)
                )


async def _seed_samples(
    factory: async_sessionmaker[AsyncSession],
    channel_id: int,
    specs: list[tuple[str, str]],
) -> None:
    """Seed ParserSample rows.

    Args:
        factory: session factory.
        channel_id: target channel.
        specs: list of (text_suffix, parsed_by) tuples.
    """
    async with factory() as session:
        async with session.begin():
            for _i, (suffix, parsed_by) in enumerate(specs):
                text = f"LONG XAUUSD Entry 2350 SL 2342 TP 2360 {suffix}"
                session.add(
                    ParserSample(
                        channel_id=channel_id,
                        text_hash=_sha256(text),
                        text=text,
                        parsed_by=parsed_by,
                        parsed_signal={
                            "symbol": "XAUUSD",
                            "side": "LONG",
                            "entry": 2350.0,
                            "sl": 2342.0,
                            "tp": [2360.0],
                            "leverage": None,
                        },
                        confidence=0.95,
                    )
                )


async def _seed_active_parser(
    factory: async_sessionmaker[AsyncSession],
    channel_id: int,
) -> Parser:
    async with factory() as session:
        async with session.begin():
            from tg_listener.db.repos.parsers import ParserRepo

            repo = ParserRepo(session)
            parser = await repo.propose(
                channel_id=channel_id,
                regex_table=_TABLE,
                source="seed",
                notes="stats test",
            )
            parser = await repo.activate(parser.id)
    async with factory() as session:
        result = await session.execute(select(Parser).where(Parser.id == parser.id))
        return result.scalar_one()


async def _seed_eval_run(
    factory: async_sessionmaker[AsyncSession],
    parser_id: int,
    samples_total: int,
    samples_matched: int,
    disagreements: list[dict[str, Any]],
) -> ParserEvalRun:
    async with factory() as session:
        async with session.begin():
            from tg_listener.db.repos.eval_runs import EvalRunRepo

            repo = EvalRunRepo(session)
            run = await repo.record(
                parser_id=parser_id,
                samples_total=samples_total,
                samples_matched=samples_matched,
                disagreements=disagreements,
            )
    async with factory() as session:
        result = await session.execute(select(ParserEvalRun).where(ParserEvalRun.id == run.id))
        return result.scalar_one()


def _capture_stdout_main(argv: list[str]) -> tuple[int, dict[str, Any]]:
    buf = StringIO()
    with patch("sys.stdout", buf):
        rc = main(argv)
    output = buf.getvalue().strip()
    return rc, json.loads(output)


# ── Tests: parser stats ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stats_empty_channel(
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty channel → samples_total=0, samples_by_parsed_by={}, active_parser=None."""
    # Seed channel chưa có sample.
    await _seed_channel(db_session_factory)

    import tg_listener.cli.parser_cmd as cmd_module

    monkeypatch.setattr(cmd_module, "_make_session_factory", lambda: db_session_factory)

    rc, data = _capture_stdout_main(["parser", "stats", "--channel-id", str(CHAN_ID)])

    assert rc == 0
    assert data["status"] == "ok"
    assert data["channel_id"] == CHAN_ID
    assert data["samples_total"] == 0
    assert data["samples_by_parsed_by"] == {}
    assert data["active_parser"] is None


@pytest.mark.asyncio
async def test_stats_fully_populated(
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Channel with mixed samples + active parser + one eval run → full dict populated."""
    await _seed_channel(db_session_factory)
    # 3 tier3_llm + 2 heuristic.
    await _seed_samples(
        db_session_factory,
        CHAN_ID,
        [
            ("s0", "tier3_llm"),
            ("s1", "tier3_llm"),
            ("s2", "tier3_llm"),
            ("s3", "heuristic"),
            ("s4", "heuristic"),
        ],
    )
    parser = await _seed_active_parser(db_session_factory, CHAN_ID)
    await _seed_eval_run(
        db_session_factory,
        parser_id=parser.id,
        samples_total=5,
        samples_matched=4,
        disagreements=[{"sample_id": 1, "kind": "mismatch", "parsed": {}, "expected": {}}],
    )

    import tg_listener.cli.parser_cmd as cmd_module

    monkeypatch.setattr(cmd_module, "_make_session_factory", lambda: db_session_factory)

    rc, data = _capture_stdout_main(["parser", "stats", "--channel-id", str(CHAN_ID)])

    assert rc == 0
    assert data["status"] == "ok"
    assert data["samples_total"] == 5
    assert data["samples_by_parsed_by"] == {"tier3_llm": 3, "heuristic": 2}

    ap = data["active_parser"]
    assert ap is not None
    assert ap["id"] == parser.id
    assert ap["version"] == parser.version
    assert ap["source"] == parser.source

    le = ap["latest_eval"]
    assert le is not None
    assert le["samples_total"] == 5
    assert le["samples_matched"] == 4
    assert abs(le["match_rate"] - round(4 / 5, 4)) < 1e-6
    assert "ran_at" in le
    assert len(le["disagreement_sample"]) == 1


@pytest.mark.asyncio
async def test_stats_no_active_parser(
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Channel with samples but no active parser → active_parser=None, exit 0."""
    await _seed_channel(db_session_factory)
    await _seed_samples(db_session_factory, CHAN_ID, [("x0", "tier3_llm")])

    import tg_listener.cli.parser_cmd as cmd_module

    monkeypatch.setattr(cmd_module, "_make_session_factory", lambda: db_session_factory)

    rc, data = _capture_stdout_main(["parser", "stats", "--channel-id", str(CHAN_ID)])

    assert rc == 0
    assert data["active_parser"] is None
    assert data["samples_total"] == 1


@pytest.mark.asyncio
async def test_stats_latest_eval_none_when_no_runs(
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Active parser with no eval runs → latest_eval=None."""
    await _seed_channel(db_session_factory)
    await _seed_active_parser(db_session_factory, CHAN_ID)

    import tg_listener.cli.parser_cmd as cmd_module

    monkeypatch.setattr(cmd_module, "_make_session_factory", lambda: db_session_factory)

    rc, data = _capture_stdout_main(["parser", "stats", "--channel-id", str(CHAN_ID)])

    assert rc == 0
    ap = data["active_parser"]
    assert ap is not None
    assert ap["latest_eval"] is None


@pytest.mark.asyncio
async def test_stats_disagreement_sample_truncated_to_3(
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """disagreement_sample is capped at 3 entries even when run has 5 disagreements."""
    await _seed_channel(db_session_factory)
    parser = await _seed_active_parser(db_session_factory, CHAN_ID)

    five_disagreements = [
        {"sample_id": i, "kind": "mismatch", "parsed": {}, "expected": {}}
        for i in range(5)
    ]
    await _seed_eval_run(
        db_session_factory,
        parser_id=parser.id,
        samples_total=10,
        samples_matched=5,
        disagreements=five_disagreements,
    )

    import tg_listener.cli.parser_cmd as cmd_module

    monkeypatch.setattr(cmd_module, "_make_session_factory", lambda: db_session_factory)

    rc, data = _capture_stdout_main(["parser", "stats", "--channel-id", str(CHAN_ID)])

    assert rc == 0
    le = data["active_parser"]["latest_eval"]
    assert le is not None
    assert len(le["disagreement_sample"]) == 3


# ── Tests: _run_induce end-to-end — ParserEvalRun created ─────────────────────


@pytest.mark.asyncio
async def test_induce_creates_eval_run_row(
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After successful induce, a ParserEvalRun row exists for the new parser."""
    monkeypatch.setenv("INDUCTION_PROVIDER", "stub")

    # Seed channel và samples.
    chan_id = -100777888999
    async with db_session_factory() as session:
        async with session.begin():
            session.add(Channel(id=chan_id, name="test_evalrun_chan", auto_approve=False))
            await session.flush()
            for i in range(5):
                text = f"LONG XAUUSD Entry 2350 SL 2342 TP 2360 evalrun {i}"
                session.add(
                    ParserSample(
                        channel_id=chan_id,
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
                        },
                        confidence=0.95,
                    )
                )

    import tg_listener.cli.parser_cmd as cmd_module

    monkeypatch.setattr(cmd_module, "_make_session_factory", lambda: db_session_factory)

    rc = main(["parser", "induce", "--channel-id", str(chan_id)])
    assert rc == 0

    # Fetch the created parser.
    async with db_session_factory() as session:
        parser_result = await session.execute(
            select(Parser).where(Parser.channel_id == chan_id)
        )
        parsers = list(parser_result.scalars().all())
    assert len(parsers) == 1
    parser_id = parsers[0].id

    # Phải có ít nhất 1 ParserEvalRun với samples_total > 0.
    async with db_session_factory() as session:
        run_result = await session.execute(
            select(ParserEvalRun).where(ParserEvalRun.parser_id == parser_id)
        )
        runs = list(run_result.scalars().all())

    assert len(runs) == 1
    assert runs[0].samples_total > 0
    assert runs[0].samples_matched >= 0
