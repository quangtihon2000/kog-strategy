"""ZoneSignal dataclass — the single source of truth for signal shape."""

import json
import time
from dataclasses import asdict, dataclass, field
from typing import List


@dataclass
class ZoneSignal:
    symbol: str
    redbox_upper: float
    redbox_lower: float
    targets_above: List[float]
    targets_below: List[float]
    timestamp: int = field(default_factory=lambda: int(time.time()))

    # ------------------------------------------------------------------
    def validate(self) -> None:
        if self.redbox_upper <= self.redbox_lower:
            raise ValueError(
                f"redbox_upper ({self.redbox_upper}) must be > redbox_lower ({self.redbox_lower})"
            )
        if not self.targets_above:
            raise ValueError("targets_above is empty")
        if not self.targets_below:
            raise ValueError("targets_below is empty")

    # ------------------------------------------------------------------
    @classmethod
    def from_dict(cls, d: dict) -> "ZoneSignal":
        """
        Build a ZoneSignal from a flat Redis Stream field dict.

        Expected keys:
            symbol        : str          e.g. "XAUUSD"
            redbox_upper  : str (float)  e.g. "2350.00"
            redbox_lower  : str (float)  e.g. "2340.00"
            targets_above : str          e.g. "2360.0,2370.0"  (comma-separated)
            targets_below : str          e.g. "2330.0,2320.0"  (comma-separated)
        """
        return cls(
            symbol=d["symbol"],
            redbox_upper=float(d["redbox_upper"]),
            redbox_lower=float(d["redbox_lower"]),
            targets_above=[float(x) for x in str(d["targets_above"]).split(",")],
            targets_below=[float(x) for x in str(d["targets_below"]).split(",")],
        )

    # ------------------------------------------------------------------
    def to_json(self) -> str:
        """Serialize to the JSON format expected by ZoneSignalEA.mq5."""
        return json.dumps(asdict(self), ensure_ascii=True)
