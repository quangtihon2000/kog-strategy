"""CondeSignal dataclass — matches the JSON shape expected by CondeAutoEntryEA.mq5."""

import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from typing import List, Optional


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
    channel_id: Optional[int] = None  # Telegram channel BIGINT — gate key (rename-safe); optional for legacy

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
        if self.timestamp <= 0:
            raise ValueError(f"timestamp must be > 0, got {self.timestamp}")
        # Per-element và monotonic check chuyển sang sanitize() để cứu được signal
        # bị OCR producer nhặt sai 1 con số (vd nhặt entry vào danh sách TP).
        # tps rỗng cũng được chấp nhận — EA sẽ fallback ATR/Fixed-TP khi
        # InpAllowMissingTp=true.

    # ------------------------------------------------------------------
    def sanitize(self) -> List[float]:
        """
        Loại bỏ các TP không hợp lệ và sắp xếp theo hướng giao dịch.

        Drop: tp <= 0, tp sai hướng (BUY: tp <= entry; SELL: tp >= entry), trùng lặp.
        Sau khi drop, sort ascending cho BUY / descending cho SELL.
        Trả về list các TP đã bị drop (để caller log WARN audit).

        Sau sanitize, self.tps có thể rỗng — caller hợp lệ hoá bằng nhánh
        InpAllowMissingTp của EA (ATR/Fixed-TP fallback).
        """
        dropped: List[float] = []
        kept: List[float] = []
        seen: set = set()
        for tp in self.tps:
            if tp <= 0:
                dropped.append(tp)
                continue
            if self.direction == "BUY" and tp <= self.entry_price:
                dropped.append(tp)
                continue
            if self.direction == "SELL" and tp >= self.entry_price:
                dropped.append(tp)
                continue
            key = round(tp, 5)
            if key in seen:
                dropped.append(tp)
                continue
            seen.add(key)
            kept.append(tp)
        kept.sort(reverse=(self.direction == "SELL"))
        self.tps = kept
        return dropped

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
        channel_id_raw = str(d.get("channel_id", "")).strip()
        try:
            channel_id = int(channel_id_raw) if channel_id_raw else None
        except ValueError:
            channel_id = None
        return cls(
            timestamp=int(d["timestamp"]),
            symbol=d["symbol"],
            direction=str(d["direction"]).upper(),
            entry_price=float(d["entry_price"]),
            sl=float(d["sl"]),
            tps=[float(x) for x in str(d["tps"]).split(",") if x.strip()],
            channel_name=_clean_channel_name(str(d["channel_name"])),
            channel_id=channel_id,
        )

    # ------------------------------------------------------------------
    def to_json(self) -> str:
        """Serialize to the JSON format expected by CondeAutoEntryEA.mq5."""
        return json.dumps(asdict(self), ensure_ascii=True)
