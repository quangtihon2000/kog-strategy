"""Channel A parser - clean English-format signals. Spec section 5.4.

Channel A uses a structured, label-based layout:
    LONG XAUUSD
    Entry: 2350  (or Entry zone 2350-2355)
    SL: 2342
    TP1: 2362
    TP2: 2370
    Leverage: 10  (optional)
"""

from __future__ import annotations

import re

from tg_listener.models import EntryValue, ParsedSignalFields
from tg_listener.parsers.numbers import _norm_num

CHANNEL_A_ID: int = -1001234567890

# Matches hyphen-minus, en-dash (U+2013), em-dash (U+2014) as zone separators.
_DASH_PAT = "[-\u2013\u2014]"


class ChannelAParser:
    """Parser for Channel A Pro clean-format signals."""

    channel_id: int = CHANNEL_A_ID
    name: str = "Channel A Pro"

    _SIDE_RE = re.compile(r"(LONG|SHORT)\s+(\w+)", re.IGNORECASE)
    _ENTRY_ZONE_RE = re.compile(
        r"Entry\s*(?:zone\s*)?[:=\-]?\s*([\d,. ]+?)\s*"
        + _DASH_PAT
        + r"\s*([\d,.]+)",
        re.IGNORECASE,
    )
    _ENTRY_RE = re.compile(r"Entry\s*[:=\-]?\s*([\d,.]+[kKmM]?)", re.IGNORECASE)
    _SL_RE = re.compile(r"SL\s*[:=\-]?\s*([\d,.]+[kKmM]?)", re.IGNORECASE)
    _TP_RE = re.compile(r"TP\d*\s*[:=\-]?\s*([\d,.]+[kKmM]?)", re.IGNORECASE)
    _LEV_RE = re.compile(r"(?:Leverage|Lev|x)\s*[:=\-]?\s*(\d+)", re.IGNORECASE)

    def parse(self, text: str) -> ParsedSignalFields | None:
        side_m = self._SIDE_RE.search(text)
        if not side_m:
            return None

        symbol = side_m.group(2).upper()
        side = side_m.group(1).upper()

        entry: EntryValue
        zone_m = self._ENTRY_ZONE_RE.search(text)
        if zone_m:
            lo_raw = zone_m.group(1).strip()
            hi_raw = zone_m.group(2).strip()
            entry = (_norm_num(lo_raw), _norm_num(hi_raw))
        else:
            entry_m = self._ENTRY_RE.search(text)
            if not entry_m:
                return None
            entry = _norm_num(entry_m.group(1))

        sl_m = self._SL_RE.search(text)
        if not sl_m:
            return None
        sl = _norm_num(sl_m.group(1))

        tp_strs = self._TP_RE.findall(text)
        if not tp_strs:
            return None
        tp = [_norm_num(x) for x in tp_strs]

        lev_m = self._LEV_RE.search(text)
        leverage = int(lev_m.group(1)) if lev_m else None

        return ParsedSignalFields(
            symbol=symbol,
            side=side,  # type: ignore[arg-type]
            entry=entry,
            sl=sl,
            tp=tp,
            leverage=leverage,
        )
