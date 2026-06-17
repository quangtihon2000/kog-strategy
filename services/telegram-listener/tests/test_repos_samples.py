"""Tests for SampleRepo — text_hash dedup, list, count."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tg_listener.db.models import Channel
from tg_listener.db.repos.samples import SampleRepo


async def _make_channel(session: AsyncSession, channel_id: int) -> None:
    ch = Channel(id=channel_id, name=f"Ch {channel_id}")
    session.add(ch)
    await session.flush()


_SIGNAL = {"symbol": "BTC", "side": "LONG", "entry": 67500.0, "sl": 66800.0, "tp": [68200.0]}


@pytest.mark.asyncio
async def test_insert_if_new_creates_sample(db_session: AsyncSession) -> None:
    await _make_channel(db_session, -3001)
    repo = SampleRepo(db_session)
    sample = await repo.insert_if_new(-3001, "hello world", "regex", _SIGNAL, 1.0)
    assert sample is not None
    assert sample.channel_id == -3001
    assert sample.text == "hello world"


@pytest.mark.asyncio
async def test_insert_if_new_dedup(db_session: AsyncSession) -> None:
    """Second insert of same text returns None."""
    await _make_channel(db_session, -3002)
    repo = SampleRepo(db_session)
    s1 = await repo.insert_if_new(-3002, "same text", "regex", _SIGNAL, 1.0)
    s2 = await repo.insert_if_new(-3002, "same text", "regex", _SIGNAL, 1.0)
    assert s1 is not None
    assert s2 is None  # duplicate, ON CONFLICT DO NOTHING


@pytest.mark.asyncio
async def test_count(db_session: AsyncSession) -> None:
    await _make_channel(db_session, -3003)
    repo = SampleRepo(db_session)
    assert await repo.count(-3003) == 0
    await repo.insert_if_new(-3003, "text1", "regex", _SIGNAL, 1.0)
    await repo.insert_if_new(-3003, "text2", "regex", _SIGNAL, 1.0)
    assert await repo.count(-3003) == 2


@pytest.mark.asyncio
async def test_list_for_channel(db_session: AsyncSession) -> None:
    await _make_channel(db_session, -3004)
    repo = SampleRepo(db_session)
    for i in range(5):
        await repo.insert_if_new(-3004, f"text {i}", "regex", _SIGNAL, 1.0)
    samples = await repo.list_for_channel(-3004)
    assert len(samples) == 5


@pytest.mark.asyncio
async def test_list_for_channel_limit(db_session: AsyncSession) -> None:
    await _make_channel(db_session, -3005)
    repo = SampleRepo(db_session)
    for i in range(10):
        await repo.insert_if_new(-3005, f"text {i}", "regex", _SIGNAL, 1.0)
    samples = await repo.list_for_channel(-3005, limit=3)
    assert len(samples) == 3
