"""Tests for ParserRepo — versioning, single-active invariant, propose→activate."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tg_listener.db.models import Channel
from tg_listener.db.repos.parsers import ParserRepo


async def _make_channel(session: AsyncSession, channel_id: int) -> None:
    ch = Channel(id=channel_id, name=f"Ch {channel_id}")
    session.add(ch)
    await session.flush()


@pytest.mark.asyncio
async def test_get_active_returns_none_when_empty(db_session: AsyncSession) -> None:
    await _make_channel(db_session, -2001)
    repo = ParserRepo(db_session)
    result = await repo.get_active(-2001)
    assert result is None


@pytest.mark.asyncio
async def test_propose_creates_version_1(db_session: AsyncSession) -> None:
    await _make_channel(db_session, -2002)
    repo = ParserRepo(db_session)
    parser = await repo.propose(-2002, {"key": "val"})
    assert parser.version == 1
    assert parser.status == "proposed"
    assert parser.source == "manual"


@pytest.mark.asyncio
async def test_propose_increments_version(db_session: AsyncSession) -> None:
    await _make_channel(db_session, -2003)
    repo = ParserRepo(db_session)
    p1 = await repo.propose(-2003, {})
    p2 = await repo.propose(-2003, {})
    assert p1.version == 1
    assert p2.version == 2


@pytest.mark.asyncio
async def test_activate_sets_active(db_session: AsyncSession) -> None:
    await _make_channel(db_session, -2004)
    repo = ParserRepo(db_session)
    parser = await repo.propose(-2004, {})
    activated = await repo.activate(parser.id)
    assert activated.status == "active"
    assert activated.activated_at is not None


@pytest.mark.asyncio
async def test_activate_retires_previous_active(db_session: AsyncSession) -> None:
    await _make_channel(db_session, -2005)
    repo = ParserRepo(db_session)
    p1 = await repo.propose(-2005, {})
    await repo.activate(p1.id)
    # Verify active.
    active = await repo.get_active(-2005)
    assert active is not None and active.id == p1.id

    # Now propose and activate p2 — p1 should become retired.
    p2 = await repo.propose(-2005, {})
    await repo.activate(p2.id)

    active2 = await repo.get_active(-2005)
    assert active2 is not None and active2.id == p2.id

    versions = await repo.list_versions(-2005)
    p1_refreshed = next(p for p in versions if p.id == p1.id)
    assert p1_refreshed.status == "retired"


@pytest.mark.asyncio
async def test_reject_parser(db_session: AsyncSession) -> None:
    await _make_channel(db_session, -2006)
    repo = ParserRepo(db_session)
    parser = await repo.propose(-2006, {})
    rejected = await repo.reject(parser.id)
    assert rejected.status == "rejected"


@pytest.mark.asyncio
async def test_activate_missing_raises(db_session: AsyncSession) -> None:
    repo = ParserRepo(db_session)
    with pytest.raises(ValueError, match="not found"):
        await repo.activate(999999999)


@pytest.mark.asyncio
async def test_list_versions_ordered(db_session: AsyncSession) -> None:
    await _make_channel(db_session, -2007)
    repo = ParserRepo(db_session)
    for _ in range(3):
        await repo.propose(-2007, {})
    versions = await repo.list_versions(-2007)
    assert [v.version for v in versions] == [1, 2, 3]
