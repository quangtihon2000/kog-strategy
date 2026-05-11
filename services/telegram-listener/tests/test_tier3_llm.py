"""Tests for Tier 3 LLM extractor — tier3_llm.py."""

from __future__ import annotations

import hashlib

import fakeredis.aioredis

from tg_listener.models import ParsedSignalFields
from tg_listener.tiers.llm.base import ProviderError
from tg_listener.tiers.llm.cache import Tier3Cache
from tg_listener.tiers.llm.stub import StubLLMProvider, TimeoutMarker
from tg_listener.tiers.tier3_llm import _sha256, extract_tier3

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SIGNAL_RESPONSE = {
    "is_signal": True,
    "symbol": "XAUUSD",
    "side": "LONG",
    "entry": 2350.0,
    "sl": 2330.0,
    "tp": [2370.0, 2390.0],
    "leverage": None,
    "confidence": 0.9,
}

_NOT_SIGNAL_RESPONSE = {"is_signal": False}


async def _make_cache() -> Tier3Cache:
    r = await fakeredis.aioredis.FakeRedis()
    return Tier3Cache(r)


# ---------------------------------------------------------------------------
# Core extraction tests
# ---------------------------------------------------------------------------


async def test_cache_miss_provider_called_returns_fields() -> None:
    stub = StubLLMProvider([_SIGNAL_RESPONSE])
    cache = await _make_cache()
    result = await extract_tier3("XAUUSD LONG 2350 SL 2330 TP 2370", stub, cache)
    assert isinstance(result, ParsedSignalFields)
    assert result.symbol == "XAUUSD"
    assert result.side == "LONG"
    assert stub.call_count == 1


async def test_cache_miss_not_signal_returns_none_and_is_cached() -> None:
    stub = StubLLMProvider([_NOT_SIGNAL_RESPONSE])
    cache = await _make_cache()
    text = "just a chat message"
    result = await extract_tier3(text, stub, cache)
    assert result is None
    assert stub.call_count == 1

    # Second call must hit cache — provider not called again
    stub2 = StubLLMProvider([])
    result2 = await extract_tier3(text, stub2, cache)
    assert result2 is None
    assert stub2.call_count == 0


async def test_cache_hit_signal_provider_not_called() -> None:
    stub_first = StubLLMProvider([_SIGNAL_RESPONSE])
    cache = await _make_cache()
    text = "XAUUSD LONG 2350 SL 2330 TP 2370"
    await extract_tier3(text, stub_first, cache)

    stub_second = StubLLMProvider([])
    result = await extract_tier3(text, stub_second, cache)
    assert isinstance(result, ParsedSignalFields)
    assert stub_second.call_count == 0


async def test_cache_hit_negative_provider_not_called() -> None:
    stub_first = StubLLMProvider([_NOT_SIGNAL_RESPONSE])
    cache = await _make_cache()
    text = "not a signal at all"
    await extract_tier3(text, stub_first, cache)

    stub_second = StubLLMProvider([])
    result = await extract_tier3(text, stub_second, cache)
    assert result is None
    assert stub_second.call_count == 0


async def test_timeout_once_then_success() -> None:
    stub = StubLLMProvider([TimeoutMarker(), _SIGNAL_RESPONSE])
    result = await extract_tier3("buy XAUUSD", stub, None)
    assert isinstance(result, ParsedSignalFields)
    assert stub.call_count == 2


async def test_all_three_attempts_timeout_returns_none() -> None:
    stub = StubLLMProvider([TimeoutMarker(), TimeoutMarker(), TimeoutMarker()])
    result = await extract_tier3("buy XAUUSD", stub, None)
    assert result is None
    assert stub.call_count == 3


async def test_provider_raises_error_three_times_returns_none() -> None:
    stub = StubLLMProvider(
        [ProviderError("fail"), ProviderError("fail"), ProviderError("fail")]
    )
    result = await extract_tier3("buy XAUUSD", stub, None)
    assert result is None
    assert stub.call_count == 3


async def test_malformed_response_missing_fields_when_is_signal_true() -> None:
    # is_signal=True but no symbol/sl/tp — should return None gracefully
    stub = StubLLMProvider([{"is_signal": True, "confidence": 0.8}])
    result = await extract_tier3("buy gold", stub, None)
    assert result is None


async def test_invalid_side_value_returns_none() -> None:
    bad = {**_SIGNAL_RESPONSE, "side": "NEUTRAL"}
    stub = StubLLMProvider([bad])
    result = await extract_tier3("buy gold", stub, None)
    assert result is None


async def test_low_confidence_still_returns_parsed_fields() -> None:
    """Tier 3 returns the fields; orchestrator gates on confidence, not us."""
    low_conf = {**_SIGNAL_RESPONSE, "confidence": 0.5}
    stub = StubLLMProvider([low_conf])
    result = await extract_tier3("XAUUSD buy zone", stub, None)
    assert isinstance(result, ParsedSignalFields)
    assert result.confidence == 0.5


# ---------------------------------------------------------------------------
# Cache key / entry coercion
# ---------------------------------------------------------------------------


async def test_sha256_different_whitespace_yields_different_keys() -> None:
    t1 = "buy XAUUSD"
    t2 = "buy  XAUUSD"
    assert _sha256(t1) != _sha256(t2)


async def test_sha256_matches_stdlib() -> None:
    text = "hello world"
    assert _sha256(text) == hashlib.sha256(text.encode()).hexdigest()


async def test_entry_as_two_element_list_coerced_to_sorted_tuple() -> None:
    zone_resp = {**_SIGNAL_RESPONSE, "entry": [2360.0, 2350.0]}  # unsorted
    stub = StubLLMProvider([zone_resp])
    result = await extract_tier3("zone entry", stub, None)
    assert isinstance(result, ParsedSignalFields)
    assert result.entry == (2350.0, 2360.0)


async def test_entry_as_number_coerced_to_float() -> None:
    stub = StubLLMProvider([_SIGNAL_RESPONSE])
    result = await extract_tier3("single entry", stub, None)
    assert isinstance(result, ParsedSignalFields)
    assert result.entry == 2350.0
    assert isinstance(result.entry, float)


async def test_no_cache_provided_still_works() -> None:
    stub = StubLLMProvider([_SIGNAL_RESPONSE])
    result = await extract_tier3("XAUUSD LONG", stub, None)
    assert isinstance(result, ParsedSignalFields)


async def test_provider_error_then_success() -> None:
    stub = StubLLMProvider([ProviderError("oops"), _SIGNAL_RESPONSE])
    result = await extract_tier3("buy gold", stub, None)
    assert isinstance(result, ParsedSignalFields)
    assert stub.call_count == 2
