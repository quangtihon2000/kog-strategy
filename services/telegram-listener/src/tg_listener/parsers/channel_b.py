"""Channel B parser - messy Vietnamese-format signals. Spec section 5.4.

Channel B mixes Vietnamese trading terms with English keywords and emojis:
    BTC mua vung 67400 cat lo 66900 muc tieu 68200/69000 don bay x10
    Mua XAUUSD entry 2350 sl 2342 tp 2362 va 2370
    Ban ETH - entry 3520 - cat lo 3580 - target 3450,3380

Supported patterns:
- Side: Mua/Long/buy -> LONG; Ban/Short/sell -> SHORT (case-insensitive).
- Symbol: before or after side keyword.
- Entry: vung / entry keyword, supports zone (lo-hi) form.
- SL: cat lo / sl
- TPs: muc tieu / tp / target, separated by /, ,, va, or whitespace.
- Leverage: don bay x10 / lev 10 (optional).
"""

from __future__ import annotations

import re

from tg_listener.models import EntryValue, ParsedSignalFields
from tg_listener.parsers.numbers import _norm_num

CHANNEL_B_ID: int = -1009876543210

_LONG_WORDS = ("mua", "long", "buy")
_SHORT_WORDS = ("bán", "short", "sell")

_SIDE_RE = re.compile(
    r"\b(mua|long|buy|bán|short|sell)\b",
    re.IGNORECASE,
)

# Standalone symbol - uppercase 2-10 char word
_SYMBOL_RE = re.compile(r"\b([A-Z]{2,10}(?:USDT|USD|BTC)?)\b")

# Dash characters class used as string concat to avoid ambiguous char lint.
_DASH_CLASS = "[-\u2013\u2014]"

_ENTRY_ZONE_RE = re.compile(
    r"(?:vùng|entry|zone)\s*[:=\-]?\s*([\d,.]+)\s*"
    + _DASH_CLASS
    + r"\s*([\d,.]+)",
    re.IGNORECASE,
)
_ENTRY_RE = re.compile(
    r"(?:vùng|entry)\s*[:=\-]?\s*([\d,.]+[kKmM]?)",
    re.IGNORECASE,
)
_SL_RE = re.compile(
    r"(?:cắt\s*lỗ|sl|stop)\s*[:=\-]?\s*([\d,.]+[kKmM]?)",
    re.IGNORECASE,
)
# TPs: keyword followed by one or more numbers separated by /, ,, "va", whitespace.
_TP_HEAD_RE = re.compile(
    r"(?:mục\s*tiêu|target|tp\d*)\s*[:=\-]?\s*"
    r"([\d,.kKmM]+"
    r"(?:\s*[/,]\s*[\d,.kKmM]+|(?:\s+và\s+|\s+)[\d,.kKmM]+)*)",
    re.IGNORECASE,
)
# Primary splits: slash, "va", whitespace
_TP_SPLIT_PRIMARY_RE = re.compile(r"/|\bvà\b|\s+")
# Comma that is NOT a thousands separator: followed by non-3-digit group
_TP_COMMA_LIST_RE = re.compile(r",(?!\d{3}(?:[^\d]|$))")
_LEV_RE = re.compile(
    r"(?:đòn\s*bẩy\s*x?|lev\s*[:=\-]?\s*|x\s*)(\d+)",
    re.IGNORECASE,
)

_SIDE_KEYWORDS_LONG = frozenset(_LONG_WORDS)
_SIDE_KEYWORDS_SHORT = frozenset(_SHORT_WORDS)


def _resolve_side(word: str) -> str | None:
    w = word.lower()
    if w in _SIDE_KEYWORDS_LONG:
        return "LONG"
    if w in _SIDE_KEYWORDS_SHORT:
        return "SHORT"
    return None


# Common non-symbol uppercase words to skip
_SKIP_SYMBOLS = frozenset(
    (
        "MUA", "BAN", "LONG", "SHORT", "BUY", "SELL", "ENTRY", "VUNG",
        "ZONE", "SL", "TP", "TARGET", "AND", "VA", "LEV", "STOP",
    )
)

_SYMBOL_PAT = re.compile(r"^[A-Z]{2,10}$")
_NONWORD_RE = re.compile(r"[^\w]")


def _extract_symbol(text: str, side_pos: int) -> str | None:
    """Find a likely symbol token near the side keyword."""
    pre = text[:side_pos].strip()
    pre_tokens = pre.split()
    if pre_tokens:
        candidate = _NONWORD_RE.sub("", pre_tokens[-1].upper())
        if candidate and candidate not in _SKIP_SYMBOLS and _SYMBOL_PAT.match(candidate):
            return candidate

    post_match = re.search(r"\S+", text[side_pos:])
    if post_match:
        after_side = text[side_pos + post_match.end():].lstrip()
        tok_m = re.match(r"(\S+)", after_side)
        if tok_m:
            candidate = _NONWORD_RE.sub("", tok_m.group(1).upper())
            if candidate and candidate not in _SKIP_SYMBOLS and _SYMBOL_PAT.match(candidate):
                return candidate

    return None


class ChannelBParser:
    """Parser for Channel B messy Vietnamese-format signals."""

    channel_id: int = CHANNEL_B_ID
    name: str = "Channel B VN"

    def parse(self, text: str) -> ParsedSignalFields | None:
        # Strip emoji decorations for cleaner matching; keep dash chars.
        clean = re.sub(r"[^\w\s\u2013\u2014\-,./:=]", " ", text, flags=re.UNICODE)

        side_m = _SIDE_RE.search(clean)
        if not side_m:
            return None

        side = _resolve_side(side_m.group(1))
        if not side:
            return None

        symbol = _extract_symbol(clean, side_m.start())
        if not symbol:
            for tok in _SYMBOL_RE.findall(text.upper()):
                if tok not in _SKIP_SYMBOLS:
                    symbol = tok
                    break
        if not symbol:
            return None

        entry: EntryValue
        zone_m = _ENTRY_ZONE_RE.search(clean)
        if zone_m:
            entry = (_norm_num(zone_m.group(1)), _norm_num(zone_m.group(2)))
        else:
            entry_m = _ENTRY_RE.search(clean)
            if not entry_m:
                return None
            entry = _norm_num(entry_m.group(1))

        sl_m = _SL_RE.search(clean)
        if not sl_m:
            return None
        sl = _norm_num(sl_m.group(1))

        # Commas that are thousands-separators (exactly 3 digits after comma)
        # are preserved; other commas become spaces (list separators).
        tp: list[float] = []
        for tp_m in _TP_HEAD_RE.finditer(clean):
            raw_tail = tp_m.group(1)
            normalized_tail = _TP_COMMA_LIST_RE.sub(" ", raw_tail)
            for part in _TP_SPLIT_PRIMARY_RE.split(normalized_tail):
                part = part.strip()
                if not part:
                    continue
                try:
                    tp.append(_norm_num(part))
                except ValueError:
                    pass
        if not tp:
            return None

        lev_m = _LEV_RE.search(clean)
        leverage = int(lev_m.group(1)) if lev_m else None

        return ParsedSignalFields(
            symbol=symbol,
            side=side,  # type: ignore[arg-type]
            entry=entry,
            sl=sl,
            tp=tp,
            leverage=leverage,
        )
