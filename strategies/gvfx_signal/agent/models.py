"""GvfxSignal dataclass — matches the JSON shape expected by GvfxSignalEA.mq5."""

import json
from dataclasses import asdict, dataclass
from typing import Any


_TRUE_TOKENS  = frozenset({"1", "true", "yes", "on", "y", "t"})
_FALSE_TOKENS = frozenset({"0", "false", "no", "off", "n", "f"})


def _parse_bool(value: Any, *, default: bool) -> bool:
    """Coerce stringly-typed Redis fields into bool. Empty/None/null → default."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s == "" or s == "null":
        return default
    if s in _TRUE_TOKENS:
        return True
    if s in _FALSE_TOKENS:
        return False
    raise ValueError(f"cannot parse {value!r} as bool")


@dataclass
class GvfxSignal:
    """
    Per-(account, symbol) target-price grid signal.

    Producer-supplied timestamp is preserved end-to-end (NOT re-stamped) — the EA
    embeds it in each position comment as `GVFX_T{ts}` for restart-safe dedup.

    Optional `low` / `high` price gates (0 = disabled): the EA only opens BUY when
    price > low and SELL when price < high.

    `use_atr` (default True): when True the EA derives effective step/tp from a
    cached iATR handle; the producer-supplied `step`/`tp` then act as fallback
    values used only when the ATR buffer is unavailable (handle invalid or
    indicator still warming up).
    """

    timestamp: int            # unix seconds — producer-supplied, authoritative
    symbol: str               # e.g. "XAUUSD"
    target: float             # target price; signal becomes inactive once reached
    direction: str            # "BUY" or "SELL"
    step: int                 # grid spacing (MT5 points) — fallback when use_atr
    tp: int                   # take-profit distance per order (MT5 points) — fallback when use_atr
    low: float = 0.0          # BUY entry floor (price). 0 = disabled
    high: float = 0.0         # SELL entry ceiling (price). 0 = disabled
    use_atr: bool = True      # EA derives step/tp from ATR; signal step/tp become fallback

    # ------------------------------------------------------------------
    def validate(self) -> None:
        if self.direction not in ("BUY", "SELL"):
            raise ValueError(f"direction must be BUY or SELL, got {self.direction!r}")
        if self.target <= 0:
            raise ValueError(f"target must be > 0, got {self.target}")
        if self.step <= 0:
            raise ValueError(f"step must be > 0, got {self.step}")
        if self.tp <= 0:
            raise ValueError(f"tp must be > 0, got {self.tp}")
        if self.timestamp <= 0:
            raise ValueError(f"timestamp must be > 0, got {self.timestamp}")
        if self.low < 0:
            raise ValueError(f"low must be >= 0, got {self.low}")
        if self.high < 0:
            raise ValueError(f"high must be >= 0, got {self.high}")
        if self.low > 0 and self.high > 0 and self.low >= self.high:
            raise ValueError(f"low ({self.low}) must be < high ({self.high})")

    # ------------------------------------------------------------------
    @classmethod
    def from_dict(cls, d: dict) -> "GvfxSignal":
        """
        Build a GvfxSignal from a flat Redis Stream field dict.

        Expected keys (all arrive as strings from Redis):
            timestamp : str (int)    e.g. "1777896356"
            symbol    : str          e.g. "XAUUSD"
            target    : str (float)  e.g. "4860.0"
            direction : str          "BUY" or "SELL"
            step      : str (int)    e.g. "500"
            tp        : str (int)    e.g. "500"
            low       : str (float)  optional, default "0"
            high      : str (float)  optional, default "0"
            use_atr   : str (bool)   optional, default "true"
                                     accepts "true"/"false"/"1"/"0"/"yes"/"no"
        """
        return cls(
            timestamp=int(d["timestamp"]),
            symbol=d["symbol"],
            target=float(d["target"]),
            direction=str(d["direction"]).upper(),
            step=int(d["step"]),
            tp=int(d["tp"]),
            low=float(d.get("low", 0) or 0),
            high=float(d.get("high", 0) or 0),
            use_atr=_parse_bool(d.get("use_atr"), default=True),
        )

    # ------------------------------------------------------------------
    def to_json(self) -> str:
        """Serialize to the JSON format expected by GvfxSignalEA.mq5."""
        return json.dumps(asdict(self), ensure_ascii=True)
