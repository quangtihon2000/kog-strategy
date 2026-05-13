"""ZoneSignal dataclass — the single source of truth for signal shape."""

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from typing import List

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "shared"))
from agent_lib.timefmt import now_unix  # noqa: E402


@dataclass
class ZoneSignal:
    symbol: str
    redbox_upper: float
    redbox_lower: float
    targets_above: List[float]
    targets_below: List[float]
    timestamp: int = field(default_factory=now_unix)

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

        # Direction-aware monotonic ordering: targets must walk away from the
        # redbox (T1 closest, T2 farther, …). The OCR producer occasionally
        # swaps a digit, leaving an outlier inside or out-of-order which slips
        # past the empty/positivity checks. Rejecting here makes it surface as
        # a `Bad message` so the operator can fix-and-republish via the
        # Telegram badmsg Edit UI.
        prev = self.redbox_upper
        for i, t in enumerate(self.targets_above):
            if t <= prev:
                ref = "redbox_upper" if i == 0 else f"targets_above[{i - 1}]"
                raise ValueError(
                    f"targets_above must be strictly ascending and > redbox_upper; "
                    f"targets_above[{i}]={t} <= {ref}={prev}"
                )
            prev = t
        prev = self.redbox_lower
        for i, t in enumerate(self.targets_below):
            if t >= prev:
                ref = "redbox_lower" if i == 0 else f"targets_below[{i - 1}]"
                raise ValueError(
                    f"targets_below must be strictly descending and < redbox_lower; "
                    f"targets_below[{i}]={t} >= {ref}={prev}"
                )
            prev = t

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
