"""Tests for Tier3Cache using fakeredis."""

from __future__ import annotations

import fakeredis.aioredis

from tg_listener.models import ParsedSignalFields
from tg_listener.tiers.llm.cache import CacheLookup, Tier3Cache

_FIELDS = ParsedSignalFields(
    symbol="XAUUSD",
    side="LONG",
    entry=2350.0,
    sl=2330.0,
    tp=[2370.0, 2390.0],
    leverage=None,
    confidence=0.9,
)


async def _make_cache(  # type: ignore[type-arg]
    namespace: str = "tier3:llm",
) -> tuple[Tier3Cache, fakeredis.aioredis.FakeRedis]:
    r = await fakeredis.aioredis.FakeRedis()
    return Tier3Cache(r, ttl_seconds=3600, namespace=namespace), r


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


async def test_set_get_parsed_signal_fields() -> None:
    cache, _ = await _make_cache()
    await cache.set("abc123", _FIELDS)
    lookup = await cache.get("abc123")
    assert lookup.hit is True
    assert lookup.value is not None
    assert lookup.value.symbol == "XAUUSD"
    assert lookup.value.confidence == 0.9


async def test_set_get_none_negative_cache() -> None:
    cache, _ = await _make_cache()
    await cache.set("negkey", None)
    lookup = await cache.get("negkey")
    assert lookup.hit is True
    assert lookup.value is None


async def test_miss_returns_cache_lookup_hit_false() -> None:
    cache, _ = await _make_cache()
    lookup = await cache.get("notexist")
    assert lookup.hit is False
    assert lookup.value is None


# ---------------------------------------------------------------------------
# TTL
# ---------------------------------------------------------------------------


async def test_ttl_is_set_after_set_call() -> None:
    cache, r = await _make_cache()
    await cache.set("ttlkey", _FIELDS)
    ttl = await r.ttl("tier3:llm:ttlkey")
    assert ttl > 0


async def test_ttl_not_negative_for_negative_cache() -> None:
    cache, r = await _make_cache()
    await cache.set("ttlneg", None)
    ttl = await r.ttl("tier3:llm:ttlneg")
    assert ttl > 0


# ---------------------------------------------------------------------------
# Namespace prefix
# ---------------------------------------------------------------------------


async def test_namespace_prefix_applied() -> None:
    cache, r = await _make_cache(namespace="myns:test")
    await cache.set("sha1", _FIELDS)
    raw = await r.get("myns:test:sha1")
    assert raw is not None


async def test_different_namespaces_isolated() -> None:
    cache_a, r = await _make_cache(namespace="ns:a")
    cache_b = Tier3Cache(r, namespace="ns:b")
    await cache_a.set("key", _FIELDS)
    lookup = await cache_b.get("key")
    assert lookup.hit is False


# ---------------------------------------------------------------------------
# CacheLookup dataclass
# ---------------------------------------------------------------------------


def test_cache_lookup_hit_true() -> None:
    lk = CacheLookup(hit=True, value=_FIELDS)
    assert lk.hit is True
    assert lk.value is _FIELDS


def test_cache_lookup_miss() -> None:
    lk = CacheLookup(hit=False)
    assert lk.value is None


# ---------------------------------------------------------------------------
# Entry tuple round-trip
# ---------------------------------------------------------------------------


async def test_tuple_entry_survives_round_trip() -> None:
    fields_with_zone = ParsedSignalFields(
        symbol="BTCUSDT",
        side="SHORT",
        entry=(67000.0, 67500.0),
        sl=68000.0,
        tp=[66000.0],
        confidence=0.8,
    )
    cache, _ = await _make_cache()
    await cache.set("zone", fields_with_zone)
    lookup = await cache.get("zone")
    assert lookup.hit is True
    assert lookup.value is not None
    assert lookup.value.entry == (67000.0, 67500.0)
