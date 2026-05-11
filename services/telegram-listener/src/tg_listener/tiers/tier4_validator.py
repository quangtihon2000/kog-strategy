"""Tier 4 — semantic validator. Spec section 5.6.

Final gate before signals reach Redis. Checks direction logic, symbol
whitelist, entry-vs-market sanity, SL distance, R:R, and leverage bounds.
"""

from __future__ import annotations

from typing import ClassVar, Protocol, runtime_checkable

from tg_listener.models import ParsedSignalFields, ValidationResult


@runtime_checkable
class MarketDataProvider(Protocol):
    async def get_known_symbols(self) -> frozenset[str]: ...

    async def get_market_price(self, symbol: str) -> float: ...


_KNOWN: frozenset[str] = frozenset(
    {"BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XAUUSD", "XAGUSD"}
)

_PRICES: dict[str, float] = {
    "BTCUSDT": 67500.0,
    "ETHUSDT": 3520.0,
    "SOLUSDT": 142.5,
    "BNBUSDT": 612.0,
    "XAUUSD": 2358.0,
    "XAGUSD": 28.5,
}


class StubMarketDataProvider:
    _KNOWN: ClassVar[frozenset[str]] = _KNOWN
    _PRICES: ClassVar[dict[str, float]] = dict(_PRICES)

    async def get_known_symbols(self) -> frozenset[str]:
        return self._KNOWN

    async def get_market_price(self, symbol: str) -> float:
        return self._PRICES[symbol]


async def validate(s: ParsedSignalFields, provider: MarketDataProvider) -> ValidationResult:
    """Validate a parsed signal against market data. Spec §5.6."""
    # 1. Direction logic
    if s.side == "LONG":
        entry_low = s.entry if isinstance(s.entry, float) else min(s.entry)
        if not (s.sl < entry_low < min(s.tp)):
            return ValidationResult(ok=False, reason="long_levels_inverted")
    else:  # SHORT
        entry_high = s.entry if isinstance(s.entry, float) else max(s.entry)
        if not (max(s.tp) < entry_high < s.sl):
            return ValidationResult(ok=False, reason="short_levels_inverted")

    # 2. Symbol whitelist
    if s.symbol not in await provider.get_known_symbols():
        return ValidationResult(ok=False, reason=f"unknown_symbol_{s.symbol}")

    # 3. Entry ≤ 5% from market price
    market = await provider.get_market_price(s.symbol)
    entry_mid = s.entry if isinstance(s.entry, float) else sum(s.entry) / 2
    deviation = (entry_mid - market) / market
    if abs(deviation) > 0.05:
        return ValidationResult(ok=False, reason=f"entry_too_far_{deviation:.2%}")

    # 4. SL distance (0.1% - 20%)
    sl_pct = abs(entry_mid - s.sl) / entry_mid
    if not (0.001 < sl_pct < 0.20):
        return ValidationResult(ok=False, reason=f"unrealistic_sl_{sl_pct:.2%}")

    # 5. R:R minimum 0.5
    rr = abs(s.tp[0] - entry_mid) / abs(entry_mid - s.sl)
    if rr < 0.5:
        return ValidationResult(ok=False, reason=f"poor_rr_{rr:.2f}")

    # 6. Leverage 1..125
    if s.leverage is not None and not (1 <= s.leverage <= 125):
        return ValidationResult(ok=False, reason=f"invalid_leverage_{s.leverage}")

    return ValidationResult(ok=True, reason="ok")
