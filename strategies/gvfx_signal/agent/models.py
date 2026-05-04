"""GvfxSignal dataclass — matches the JSON shape expected by GvfxSignalEA.mq5."""

import json
from dataclasses import asdict, dataclass


@dataclass
class GvfxSignal:
    """
    Per-(account, symbol) target-price grid signal.

    Producer-supplied timestamp is preserved end-to-end (NOT re-stamped) — the EA
    embeds it in each position comment as `GVFX_T{ts}` for restart-safe dedup.
    """

    timestamp: int            # unix seconds — producer-supplied, authoritative
    symbol: str               # e.g. "XAUUSD"
    target: float             # target price; signal becomes inactive once reached
    direction: str            # "BUY" or "SELL"
    step: int                 # grid spacing (MT5 points)
    tp: int                   # take-profit distance per order (MT5 points)

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
        """
        return cls(
            timestamp=int(d["timestamp"]),
            symbol=d["symbol"],
            target=float(d["target"]),
            direction=str(d["direction"]).upper(),
            step=int(d["step"]),
            tp=int(d["tp"]),
        )

    # ------------------------------------------------------------------
    def to_json(self) -> str:
        """Serialize to the JSON format expected by GvfxSignalEA.mq5."""
        return json.dumps(asdict(self), ensure_ascii=True)
