"""Tests for induction.synthesizer — success, junk-output, and retry paths."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from tg_listener.induction.synth_provider import StubSynthProvider
from tg_listener.induction.synthesizer import SynthesizerError, synthesize
from tg_listener.parsers.regex_table import RegexTable
from tg_listener.tiers.llm.base import ProviderError

# ── Helpers ────────────────────────────────────────────────────────────────────

# Minimal valid RegexTable dict (mirrors Channel A stub).
_VALID_TABLE_DICT: dict[str, Any] = {
    "side": {
        "pattern": r"(LONG|SHORT)\s+(\w+)",
        "flags": ["IGNORECASE", "UNICODE"],
        "group": 1,
    },
    "side_map": {"long": "LONG", "short": "SHORT"},
    "symbol": None,
    "symbol_from_side_group": 2,
    "entry": {
        "pattern": r"Entry\s*[:=\-]?\s*([\d,.]+)",
        "flags": ["IGNORECASE", "UNICODE"],
        "group": 1,
    },
    "entry_zone": None,
    "sl": {
        "pattern": r"SL\s*[:=\-]?\s*([\d,.]+)",
        "flags": ["IGNORECASE", "UNICODE"],
        "group": 1,
    },
    "tp": {
        "pattern": r"TP\d*\s*[:=\-]?\s*([\d,.]+)",
        "flags": ["IGNORECASE", "UNICODE"],
        "group": 1,
    },
    "tp_split": None,
    "tp_comma_list": None,
    "leverage": None,
    "pre_clean": None,
    "skip_symbols": [],
}


def _make_samples(n: int = 5) -> list[SimpleNamespace]:
    return [
        SimpleNamespace(
            text=f"LONG XAUUSD Entry 2350 SL 2342 TP 2360 TP 2370 (sample {i})",
            parsed_signal={
                "symbol": "XAUUSD",
                "side": "LONG",
                "entry": 2350.0,
                "sl": 2342.0,
                "tp": [2360.0, 2370.0],
                "leverage": None,
            },
        )
        for i in range(n)
    ]


# ── Tests ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_synthesize_success_returns_regex_table() -> None:
    """Happy path: provider returns a valid RegexTable dict → RegexTable returned."""
    provider = StubSynthProvider(responses=[dict(_VALID_TABLE_DICT)])
    samples = _make_samples(5)

    result = await synthesize(samples, provider)

    assert isinstance(result, RegexTable)
    assert result.side.pattern == r"(LONG|SHORT)\s+(\w+)"
    assert provider.call_count == 1


@pytest.mark.asyncio
async def test_synthesize_junk_output_raises_synthesizer_error() -> None:
    """Provider returns invalid JSON structure → SynthesizerError after retry."""
    junk: dict[str, Any] = {"not_a_valid_key": 42}
    # Both the first attempt and the retry return junk.
    provider = StubSynthProvider(responses=[junk, junk])
    samples = _make_samples(5)

    with pytest.raises(SynthesizerError, match="validation failed"):
        await synthesize(samples, provider)

    # Should have been called twice (first + one retry).
    assert provider.call_count == 2


@pytest.mark.asyncio
async def test_synthesize_retry_after_validation_error() -> None:
    """First call returns garbage → ValidationError → retry returns valid table."""
    junk: dict[str, Any] = {"bad": "structure"}
    provider = StubSynthProvider(responses=[junk, dict(_VALID_TABLE_DICT)])
    samples = _make_samples(5)

    result = await synthesize(samples, provider)

    assert isinstance(result, RegexTable)
    assert provider.call_count == 2


@pytest.mark.asyncio
async def test_synthesize_provider_error_triggers_retry() -> None:
    """First call raises ProviderError → retry with valid response → success."""
    provider = StubSynthProvider(
        responses=[ProviderError("network timeout"), dict(_VALID_TABLE_DICT)]
    )
    samples = _make_samples(5)

    result = await synthesize(samples, provider)

    assert isinstance(result, RegexTable)
    assert provider.call_count == 2


@pytest.mark.asyncio
async def test_synthesize_provider_error_both_attempts_raises() -> None:
    """Both attempts raise ProviderError → SynthesizerError."""
    provider = StubSynthProvider(
        responses=[ProviderError("fail 1"), ProviderError("fail 2")]
    )
    samples = _make_samples(5)

    with pytest.raises(SynthesizerError, match="Provider failed on retry"):
        await synthesize(samples, provider)


@pytest.mark.asyncio
async def test_synthesize_no_samples_raises() -> None:
    """Empty sample list → SynthesizerError immediately."""
    provider = StubSynthProvider()

    with pytest.raises(SynthesizerError, match="No samples provided"):
        await synthesize([], provider)
