"""Tests for parser list / diff / approve / reject subcommands.

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
from tg_listener.db.models import Channel, Parser

# Skip entire module when Postgres is unavailable.
if not os.environ.get("DATABASE_URL"):
    pytest.skip("DATABASE_URL not set — skipping CLI DB tests", allow_module_level=True)

CHAN_ID = -100111222333

# Minimal valid regex_table giống _STUB_TABLE trong test_cli_induce.py.
_TABLE_V1: dict[str, Any] = {
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

# V2 khác V1 ở field "leverage" — dùng cho diff test.
_TABLE_V2: dict[str, Any] = {
    **_TABLE_V1,
    "leverage": {"pattern": r"x(\d+)", "flags": [], "group": 1},
}


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


async def _seed_channel(
    factory: async_sessionmaker[AsyncSession],
    channel_id: int = CHAN_ID,
    auto_approve: bool = False,
) -> None:
    """Insert Channel row (idempotent — skip if already exists)."""
    async with factory() as session:
        async with session.begin():
            from sqlalchemy import select as sa_select

            existing = await session.execute(
                sa_select(Channel).where(Channel.id == channel_id)
            )
            if existing.scalar_one_or_none() is None:
                session.add(
                    Channel(id=channel_id, name="test_lifecycle_chan", auto_approve=auto_approve)
                )


async def _seed_parser(
    factory: async_sessionmaker[AsyncSession],
    channel_id: int,
    regex_table: dict,  # type: ignore[type-arg]
    status: str = "proposed",
) -> Parser:
    """Insert a Parser row and return it."""
    async with factory() as session:
        async with session.begin():
            from tg_listener.db.repos.parsers import ParserRepo

            repo = ParserRepo(session)
            parser = await repo.propose(
                channel_id=channel_id,
                regex_table=regex_table,
                source="seed",
                notes="test seed",
            )
            if status == "active":
                parser = await repo.activate(parser.id)
    # Re-fetch to get committed state with auto-generated fields.
    async with factory() as session:
        result = await session.execute(select(Parser).where(Parser.id == parser.id))
        return result.scalar_one()


def _capture_stdout_main(argv: list[str]) -> tuple[int, dict]:  # type: ignore[type-arg]
    """Run main(argv) and capture its stdout JSON output."""
    buf = StringIO()
    with patch("sys.stdout", buf):
        rc = main(argv)
    output = buf.getvalue().strip()
    return rc, json.loads(output)


# ── Tests: parser list ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_parser_list_two_versions(
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """parser list returns both proposed versions ordered by version asc."""
    await _seed_channel(db_session_factory)
    await _seed_parser(db_session_factory, CHAN_ID, _TABLE_V1)
    await _seed_parser(db_session_factory, CHAN_ID, _TABLE_V2)

    import tg_listener.cli.parser_cmd as cmd_module

    monkeypatch.setattr(cmd_module, "_make_session_factory", lambda: db_session_factory)

    rc, data = _capture_stdout_main(["parser", "list", "--channel-id", str(CHAN_ID)])

    assert rc == 0
    assert data["status"] == "ok"
    assert data["channel_id"] == CHAN_ID
    assert len(data["versions"]) == 2
    # Phải được sắp xếp tăng dần theo version.
    assert data["versions"][0]["version"] < data["versions"][1]["version"]


@pytest.mark.asyncio
async def test_parser_list_unknown_channel(
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """parser list on an unknown channel returns empty versions list."""
    import tg_listener.cli.parser_cmd as cmd_module

    monkeypatch.setattr(cmd_module, "_make_session_factory", lambda: db_session_factory)

    rc, data = _capture_stdout_main(["parser", "list", "--channel-id", "-100999000111"])

    assert rc == 0
    assert data["status"] == "ok"
    assert data["versions"] == []


# ── Tests: parser diff ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_parser_diff_one_field_changed(
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """parser diff shows exactly the 'leverage' field that changed between V1 and V2."""
    await _seed_channel(db_session_factory)
    p1 = await _seed_parser(db_session_factory, CHAN_ID, _TABLE_V1)
    p2 = await _seed_parser(db_session_factory, CHAN_ID, _TABLE_V2)

    import tg_listener.cli.parser_cmd as cmd_module

    monkeypatch.setattr(cmd_module, "_make_session_factory", lambda: db_session_factory)

    rc, data = _capture_stdout_main(
        [
            "parser",
            "diff",
            "--channel-id",
            str(CHAN_ID),
            "--from",
            str(p1.version),
            "--to",
            str(p2.version),
        ]
    )

    assert rc == 0
    assert data["status"] == "ok"
    # Chỉ field "leverage" thay đổi.
    assert list(data["diff"].keys()) == ["leverage"]
    assert data["diff"]["leverage"]["from"] is None
    assert data["diff"]["leverage"]["to"] is not None


@pytest.mark.asyncio
async def test_parser_diff_missing_version(
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """parser diff with a non-existent version returns status not_found, exit 2."""
    await _seed_channel(db_session_factory)
    p1 = await _seed_parser(db_session_factory, CHAN_ID, _TABLE_V1)

    import tg_listener.cli.parser_cmd as cmd_module

    monkeypatch.setattr(cmd_module, "_make_session_factory", lambda: db_session_factory)

    rc, data = _capture_stdout_main(
        [
            "parser",
            "diff",
            "--channel-id",
            str(CHAN_ID),
            "--from",
            str(p1.version),
            "--to",
            "9999",  # version không tồn tại
        ]
    )

    assert rc == 2
    assert data["status"] == "not_found"
    assert 9999 in data["missing_versions"]


# ── Tests: parser approve ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_parser_approve_proposed(
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """parser approve activates a proposed parser and retires the previous active."""
    await _seed_channel(db_session_factory)
    # Seed parser active trước, rồi thêm proposed parser mới.
    active_p = await _seed_parser(db_session_factory, CHAN_ID, _TABLE_V1, status="active")
    proposed_p = await _seed_parser(db_session_factory, CHAN_ID, _TABLE_V2)

    import tg_listener.cli.parser_cmd as cmd_module

    monkeypatch.setattr(cmd_module, "_make_session_factory", lambda: db_session_factory)

    rc, data = _capture_stdout_main(["parser", "approve", str(proposed_p.id)])

    assert rc == 0
    assert data["status"] == "activated"
    assert data["parser_id"] == proposed_p.id

    # Kiểm tra DB: proposed đã active, active cũ đã retired.
    async with db_session_factory() as session:
        result = await session.execute(select(Parser).where(Parser.id == proposed_p.id))
        updated = result.scalar_one()
        assert updated.status == "active"

        result2 = await session.execute(select(Parser).where(Parser.id == active_p.id))
        old_active = result2.scalar_one()
        assert old_active.status == "retired"


@pytest.mark.asyncio
async def test_parser_approve_nonexistent(
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """parser approve with non-existent ID returns not_found, exit 2."""
    import tg_listener.cli.parser_cmd as cmd_module

    monkeypatch.setattr(cmd_module, "_make_session_factory", lambda: db_session_factory)

    rc, data = _capture_stdout_main(["parser", "approve", "999999999"])

    assert rc == 2
    assert data["status"] == "not_found"


# ── Tests: parser reject ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_parser_reject_proposed(
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """parser reject marks a proposed parser as rejected."""
    await _seed_channel(db_session_factory)
    p = await _seed_parser(db_session_factory, CHAN_ID, _TABLE_V1)

    import tg_listener.cli.parser_cmd as cmd_module

    monkeypatch.setattr(cmd_module, "_make_session_factory", lambda: db_session_factory)

    rc, data = _capture_stdout_main(["parser", "reject", str(p.id)])

    assert rc == 0
    assert data["status"] == "rejected"
    assert data["parser_id"] == p.id

    # Kiểm tra DB.
    async with db_session_factory() as session:
        result = await session.execute(select(Parser).where(Parser.id == p.id))
        updated = result.scalar_one()
        assert updated.status == "rejected"
