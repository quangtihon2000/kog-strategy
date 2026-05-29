"""CondeSignal dataclass — matches the JSON shape expected by CondeAutoEntryEA.mq5."""

import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from typing import List


# Smart-quote / dash / ellipsis chars that NFKC leaves intact but the
# ASCII-only filter would otherwise strip — fold them to closest ASCII first.
_PUNCT_FOLD = str.maketrans({
    "‘": "'", "’": "'",  # ‘ ’
    "‚": "'", "‛": "'",
    "“": '"', "”": '"',  # “ ”
    "„": '"', "‟": '"',
    "–": "-", "—": "-",  # – —
    "−": "-",                  # − minus
    "…": "...",                # …
    "·": ".",                  # ·
})


def _clean_channel_name(s: str) -> str:
    """NFKC-normalize, fold smart punctuation, then strip non-printable-ASCII."""
    s = unicodedata.normalize("NFKC", s).translate(_PUNCT_FOLD)
    s = re.sub(r"[^\x20-\x7E]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


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
    channel_name: str         # source channel id (e.g. Telegram channel handle); required for stats

    # ------------------------------------------------------------------
    def validate(self) -> None:
        if self.direction not in ("BUY", "SELL"):
            raise ValueError(f"direction must be BUY or SELL, got {self.direction!r}")
        if not self.channel_name or not self.channel_name.strip():
            raise ValueError("channel_name is required and must be non-empty")
        if self.entry_price <= 0:
            raise ValueError(f"entry_price must be > 0, got {self.entry_price}")
        if self.sl <= 0:
            raise ValueError(f"sl must be > 0, got {self.sl}")
        # tps rỗng được chấp nhận — EA sẽ fallback ATR/Fixed-TP nếu InpAllowMissingTp=true
        for i, tp in enumerate(self.tps):
            if tp <= 0:
                raise ValueError(f"tps[{i}] must be > 0, got {tp}")
        if self.timestamp <= 0:
            raise ValueError(f"timestamp must be > 0, got {self.timestamp}")

        # Direction-aware monotonic ordering: TP1 has the smallest expected
        # profit, TP2 larger, etc. The OCR producer occasionally swaps a digit
        # (e.g. 4719→4419) which slips past the per-tp positivity check but
        # violates monotonicity. Rejecting here makes it surface as a
        # `Bad message` so the operator can fix-and-republish via the
        # Telegram badmsg Edit UI.
        if self.direction == "BUY":
            prev = self.entry_price
            for i, tp in enumerate(self.tps):
                if tp <= prev:
                    ref = "entry_price" if i == 0 else f"tps[{i - 1}]"
                    raise ValueError(
                        f"BUY tps must be strictly ascending and > entry_price; "
                        f"tps[{i}]={tp} <= {ref}={prev}"
                    )
                prev = tp
        else:  # SELL — direction was already validated above
            prev = self.entry_price
            for i, tp in enumerate(self.tps):
                if tp >= prev:
                    ref = "entry_price" if i == 0 else f"tps[{i - 1}]"
                    raise ValueError(
                        f"SELL tps must be strictly descending and < entry_price; "
                        f"tps[{i}]={tp} >= {ref}={prev}"
                    )
                prev = tp

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
            channel_name=_clean_channel_name(str(d["channel_name"])),
        )

    # ------------------------------------------------------------------
    def to_json(self) -> str:
        """Serialize to the JSON format expected by CondeAutoEntryEA.mq5."""
        return json.dumps(asdict(self), ensure_ascii=True)
