"""Tier 4 semantic validator tests — spec §5.6."""

from __future__ import annotations

from tg_listener.models import ParsedSignalFields
from tg_listener.tiers.tier4_validator import StubMarketDataProvider, validate

_stub = StubMarketDataProvider()

# BTC @ 67500, entry window for ≤5% = [64125, 70875]
# Good LONG defaults: sl < entry < tp, SL ~1.5%, TP ~3%
_BTC_LONG_DEFAULTS: dict = {
    "symbol": "BTCUSDT",
    "side": "LONG",
    "entry": 67500.0,
    "sl": 66487.5,   # ~1.5% below
    "tp": [69525.0],  # ~3% above
}

# Good SHORT defaults: tp < entry < sl
_BTC_SHORT_DEFAULTS: dict = {
    "symbol": "BTCUSDT",
    "side": "SHORT",
    "entry": 67500.0,
    "sl": 68512.5,   # ~1.5% above
    "tp": [65475.0],  # ~3% below
}


def _signal(**kwargs) -> ParsedSignalFields:
    """Build a ParsedSignalFields with sensible LONG BTC defaults."""
    base = dict(_BTC_LONG_DEFAULTS)
    base.update(kwargs)
    return ParsedSignalFields.model_validate(base)


async def test_long_happy_path_passes() -> None:
    result = await validate(_signal(), _stub)
    assert result.ok is True
    assert result.reason == "ok"


async def test_short_happy_path_passes() -> None:
    s = ParsedSignalFields.model_validate(_BTC_SHORT_DEFAULTS)
    result = await validate(s, _stub)
    assert result.ok is True
    assert result.reason == "ok"


async def test_long_with_entry_zone_passes() -> None:
    # entry zone mid = 67500, well within 5% of stub market 67500
    s = _signal(entry=(67400.0, 67600.0))
    result = await validate(s, _stub)
    assert result.ok is True


async def test_long_levels_inverted_when_sl_above_entry() -> None:
    # sl > entry => inverted
    s = _signal(sl=68000.0, tp=[69000.0])
    result = await validate(s, _stub)
    assert result.ok is False
    assert result.reason == "long_levels_inverted"


async def test_long_levels_inverted_when_tp_below_entry() -> None:
    # tp below entry => inverted
    s = _signal(sl=66000.0, tp=[65000.0])
    result = await validate(s, _stub)
    assert result.ok is False
    assert result.reason == "long_levels_inverted"


async def test_short_levels_inverted_when_sl_below_entry() -> None:
    # sl < entry for SHORT => inverted
    s = ParsedSignalFields.model_validate(
        {**_BTC_SHORT_DEFAULTS, "sl": 66000.0, "tp": [65000.0]}
    )
    result = await validate(s, _stub)
    assert result.ok is False
    assert result.reason == "short_levels_inverted"


async def test_short_levels_inverted_when_tp_above_entry() -> None:
    # tp above entry for SHORT => inverted
    s = ParsedSignalFields.model_validate(
        {**_BTC_SHORT_DEFAULTS, "sl": 68512.5, "tp": [69000.0]}
    )
    result = await validate(s, _stub)
    assert result.ok is False
    assert result.reason == "short_levels_inverted"


async def test_unknown_symbol_rejected() -> None:
    s = _signal(symbol="DOGEUSDT", entry=0.15, sl=0.13, tp=[0.18])
    result = await validate(s, _stub)
    assert result.ok is False
    assert result.reason.startswith("unknown_symbol_")


async def test_entry_too_far_above_market() -> None:
    # 6% above 67500 = 71550
    s = _signal(entry=71550.0, sl=70000.0, tp=[73000.0])
    result = await validate(s, _stub)
    assert result.ok is False
    assert result.reason.startswith("entry_too_far_")


async def test_entry_too_far_below_market() -> None:
    # 6% below 67500 = 63450; adjust levels so direction is still valid LONG
    s = _signal(entry=63450.0, sl=62000.0, tp=[65000.0])
    result = await validate(s, _stub)
    assert result.ok is False
    assert result.reason.startswith("entry_too_far_")


async def test_unrealistic_sl_too_tight() -> None:
    # SL 0.05% away — below 0.1% threshold
    s = _signal(sl=67466.25, tp=[69525.0])  # 0.05% below 67500
    result = await validate(s, _stub)
    assert result.ok is False
    assert result.reason.startswith("unrealistic_sl_")


async def test_unrealistic_sl_too_wide() -> None:
    # SL 25% below entry
    s = _signal(sl=50625.0, tp=[69525.0])
    result = await validate(s, _stub)
    assert result.ok is False
    assert result.reason.startswith("unrealistic_sl_")


async def test_poor_rr_below_half() -> None:
    # SL 1000 away, TP only 400 away => RR = 0.4 < 0.5
    s = _signal(sl=66500.0, tp=[67900.0])
    result = await validate(s, _stub)
    assert result.ok is False
    assert result.reason.startswith("poor_rr_")


async def test_invalid_leverage_zero() -> None:
    s = _signal(leverage=0)
    result = await validate(s, _stub)
    assert result.ok is False
    assert result.reason == "invalid_leverage_0"


async def test_invalid_leverage_too_high() -> None:
    s = _signal(leverage=200)
    result = await validate(s, _stub)
    assert result.ok is False
    assert result.reason == "invalid_leverage_200"


async def test_valid_leverage_passes() -> None:
    s = _signal(leverage=10)
    result = await validate(s, _stub)
    assert result.ok is True


async def test_validator_short_circuits_on_first_failure() -> None:
    # Direction inverted AND symbol unknown — must get direction error first
    s = ParsedSignalFields.model_validate(
        {
            "symbol": "DOGEUSDT",
            "side": "LONG",
            "entry": 0.15,
            "sl": 0.20,   # sl > entry => direction fail
            "tp": [0.18],
        }
    )
    result = await validate(s, _stub)
    assert result.reason == "long_levels_inverted"


async def test_stub_provider_known_symbols_includes_btc() -> None:
    symbols = await _stub.get_known_symbols()
    assert "BTCUSDT" in symbols


async def test_stub_provider_market_price_returns_float() -> None:
    price = await _stub.get_market_price("BTCUSDT")
    assert isinstance(price, float)
    assert price > 0
