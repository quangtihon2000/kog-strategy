"""Tests for induction.SampleCollector — confidence filter, dedup, persistence."""

from __future__ import annotations

import pytest

from tg_listener.db.models import Channel
from tg_listener.induction.sample_collector import (
    CollectorConfig,
    SampleCollector,
)
from tg_listener.models import ParsedSignalFields

CHAN_ID = -1001234567890


def _make_signal(*, confidence: float = 0.95) -> ParsedSignalFields:
    return ParsedSignalFields(
        symbol="XAUUSD",
        side="LONG",
        entry=2350.0,
        sl=2342.0,
        tp=[2362.0, 2370.0],
        leverage=10,
        confidence=confidence,
    )


@pytest.mark.asyncio
async def test_collect_persists_when_confidence_above_threshold(db_session_factory) -> None:
    """Sample with confidence above threshold is persisted."""
    async with db_session_factory() as session:
        session.add(Channel(channel_id=CHAN_ID, name="A", auto_approve=False))
        await session.commit()

    collector = SampleCollector(db_session_factory)
    stored = await collector.collect(
        CHAN_ID, "LONG XAUUSD entry 2350 sl 2342 tp 2362", _make_signal()
    )
    assert stored is True


@pytest.mark.asyncio
async def test_collect_drops_low_confidence(db_session_factory) -> None:
    """Sample below confidence threshold is dropped without DB write."""
    async with db_session_factory() as session:
        session.add(Channel(channel_id=CHAN_ID, name="A", auto_approve=False))
        await session.commit()

    collector = SampleCollector(
        db_session_factory, config=CollectorConfig(confidence_threshold=0.9)
    )
    stored = await collector.collect(CHAN_ID, "text", _make_signal(confidence=0.5))
    assert stored is False


@pytest.mark.asyncio
async def test_collect_drops_near_duplicate(db_session_factory) -> None:
    """First sample stored; second nearly-identical text dropped by minhash filter."""
    async with db_session_factory() as session:
        session.add(Channel(channel_id=CHAN_ID, name="A", auto_approve=False))
        await session.commit()

    collector = SampleCollector(
        db_session_factory, config=CollectorConfig(minhash_threshold=0.7)
    )
    text_a = "LONG XAUUSD entry 2350 sl 2342 tp 2362"
    text_b = "LONG XAUUSD entry 2351 sl 2342 tp 2362"  # tiny edit, high Jaccard
    assert await collector.collect(CHAN_ID, text_a, _make_signal()) is True
    assert await collector.collect(CHAN_ID, text_b, _make_signal()) is False


@pytest.mark.asyncio
async def test_collect_keeps_diverse_text(db_session_factory) -> None:
    """Two texts with low Jaccard similarity are both persisted."""
    async with db_session_factory() as session:
        session.add(Channel(channel_id=CHAN_ID, name="A", auto_approve=False))
        await session.commit()

    collector = SampleCollector(db_session_factory)
    text_a = "LONG XAUUSD entry 2350 sl 2342 tp 2362"
    text_b = "Mua BTCUSDT vùng 67400 cắt lỗ 66900 mục tiêu 68200"
    assert await collector.collect(CHAN_ID, text_a, _make_signal()) is True
    assert await collector.collect(CHAN_ID, text_b, _make_signal()) is True


@pytest.mark.asyncio
async def test_collect_idempotent_exact_duplicate(db_session_factory) -> None:
    """Exact same text stored twice — second call returns False (sha256 dedup)."""
    async with db_session_factory() as session:
        session.add(Channel(channel_id=CHAN_ID, name="A", auto_approve=False))
        await session.commit()

    # Use threshold=0.0 so minhash won't block; sha256 unique index should deduplicate.
    collector = SampleCollector(
        db_session_factory, config=CollectorConfig(minhash_threshold=0.0)
    )
    text = "LONG XAUUSD entry 2350 sl 2342 tp 2362"
    assert await collector.collect(CHAN_ID, text, _make_signal()) is True
    assert await collector.collect(CHAN_ID, text, _make_signal()) is False
