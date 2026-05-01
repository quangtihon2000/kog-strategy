"""CondeSignal dataclass — matches the JSON shape expected by CondeAutoEntryEA.mq5."""

import json
from dataclasses import asdict, dataclass
from typing import List


@dataclass
class CondeSignal:
    """
    Per-(account, symbol) trade signal with pre-computed entry, SL, and TPs.

    The EA reads this JSON, market-fires at entry_price, and opens one position
    per TP. `timestamp` is part of the EA's dedup identity (embedded in each
    position comment as CAE_T{n}_{ts}) and MUST NOT be re-stamped by the writer.
    """

    timestamp: int            # unix seconds (GMT) — producer-supplied, authoritative
    symbol: str               # e.g. "XAUUSD"
    direction: str            # "BUY" or "SELL"
    entry_price: float
    sl: float
    tps: List[float]

    # ------------------------------------------------------------------
    def validate(self) -> None:
        if self.direction not in ("BUY", "SELL"):
            raise ValueError(f"direction must be BUY or SELL, got {self.direction!r}")
        if self.entry_price <= 0:
            raise ValueError(f"entry_price must be > 0, got {self.entry_price}")
        if self.sl <= 0:
            raise ValueError(f"sl must be > 0, got {self.sl}")
        if not self.tps:
            raise ValueError("tps is empty")
        for i, tp in enumerate(self.tps):
            if tp <= 0:
                raise ValueError(f"tps[{i}] must be > 0, got {tp}")
        if self.timestamp <= 0:
            raise ValueError(f"timestamp must be > 0, got {self.timestamp}")

    # ------------------------------------------------------------------
    @classmethod
    def from_dict(cls, d: dict) -> "CondeSignal":
        """
        Build a CondeSignal from a flat Redis Stream field dict.

        Expected keys (all arrive as strings from Redis):
            timestamp    : str (int)     e.g. "1745219000"
            symbol       : str           e.g. "XAUUSD"
            direction    : str           "BUY" or "SELL"
            entry_price  : str (float)   e.g. "2350.00"
            sl           : str (float)   e.g. "2340.00"
            tps          : str           e.g. "2355.0,2360.0,2365.0"  (comma-separated)
        """
        return cls(
            timestamp=int(d["timestamp"]),
            symbol=d["symbol"],
            direction=str(d["direction"]).upper(),
            entry_price=float(d["entry_price"]),
            sl=float(d["sl"]),
            tps=[float(x) for x in str(d["tps"]).split(",") if x.strip()],
        )

    # ------------------------------------------------------------------
    def to_json(self) -> str:
        """Serialize to the JSON format expected by CondeAutoEntryEA.mq5."""
        return json.dumps(asdict(self), ensure_ascii=True)
