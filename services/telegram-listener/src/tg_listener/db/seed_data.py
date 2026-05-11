"""Seed regex_table definitions for Channel A and Channel B.

These constants are the source of truth referenced by both:
- Alembic migration 002_seed_channel_ab.py (DB insertion)
- tests/test_regex_engine_parity.py (parity gate)

Pattern strings are identical to those compiled in channel_a.py / channel_b.py.
"""

from __future__ import annotations

# Dash character class: hyphen-minus, en-dash (U+2013), em-dash (U+2014).
# Used identically to _DASH_PAT / _DASH_CLASS in the original parsers.
_DASH_CLASS = r"[-–—]"  # noqa: RUF001

CHANNEL_A_ID: int = -1001234567890
CHANNEL_B_ID: int = -1009876543210

# ── Channel A ──────────────────────────────────────────────────────────────
# Mirrors channel_a.py exactly:
#   _SIDE_RE    = re.compile(r"(LONG|SHORT)\s+(\w+)", re.IGNORECASE)
#   _ENTRY_ZONE = Entry\s*(?:zone\s*)?[:=\-]?\s*([\d,. ]+?)\s*[-–—]\s*([\d,.]+)  # noqa: RUF003
#   _ENTRY_RE   = Entry\s*[:=\-]?\s*([\d,.]+[kKmM]?)
#   _SL_RE      = SL\s*[:=\-]?\s*([\d,.]+[kKmM]?)
#   _TP_RE      = TP\d*\s*[:=\-]?\s*([\d,.]+[kKmM]?)  (findall in original)
#   _LEV_RE     = (?:Leverage|Lev|x)\s*[:=\-]?\s*(\d+)
CHANNEL_A_REGEX_TABLE: dict = {  # type: ignore[type-arg]
    "side": {
        "pattern": r"(LONG|SHORT)\s+(\w+)",
        "flags": ["IGNORECASE", "UNICODE"],
        "group": 1,
    },
    "side_map": {
        "long": "LONG",
        "short": "SHORT",
    },
    "symbol": None,
    "symbol_from_side_group": 2,
    "entry": {
        "pattern": r"Entry\s*[:=\-]?\s*([\d,.]+[kKmM]?)",
        "flags": ["IGNORECASE", "UNICODE"],
        "group": 1,
    },
    "entry_zone": {
        # Mirrors: r"Entry\s*(?:zone\s*)?[:=\-]?\s*([\d,. ]+?)\s*" + _DASH_PAT + r"\s*([\d,.]+)"
        "pattern": r"Entry\s*(?:zone\s*)?[:=\-]?\s*([\d,. ]+?)\s*[-–—]\s*([\d,.]+)",  # noqa: RUF001
        "flags": ["IGNORECASE", "UNICODE"],
        "group": 1,
    },
    "sl": {
        "pattern": r"SL\s*[:=\-]?\s*([\d,.]+[kKmM]?)",
        "flags": ["IGNORECASE", "UNICODE"],
        "group": 1,
    },
    "tp": {
        # finditer on the same pattern; each match group 1 = one TP value.
        "pattern": r"TP\d*\s*[:=\-]?\s*([\d,.]+[kKmM]?)",
        "flags": ["IGNORECASE", "UNICODE"],
        "group": 1,
    },
    "tp_split": None,
    "tp_comma_list": None,
    "leverage": {
        "pattern": r"(?:Leverage|Lev|x)\s*[:=\-]?\s*(\d+)",
        "flags": ["IGNORECASE", "UNICODE"],
        "group": 1,
    },
    "pre_clean": None,
    "skip_symbols": [],
}

# ── Channel B ──────────────────────────────────────────────────────────────
# Mirrors channel_b.py exactly:
#   pre_clean strips emoji/non-word chars (keeps dashes, digits, letters, punct).
#   _SIDE_RE    = re.compile(r"\b(mua|long|buy|bán|short|sell)\b", IGNORECASE)
#   _SYMBOL_RE  = re.compile(r"\b([A-Z]{2,10}(?:USDT|USD|BTC)?)\b")  (searched on text.upper())
#   _ENTRY_ZONE = (?:vùng|entry|zone)\s*[:=\-]?\s*([\d,.]+)\s*[-–—]\s*([\d,.]+)  # noqa: RUF003
#   _ENTRY_RE   = (?:vùng|entry)\s*[:=\-]?\s*([\d,.]+[kKmM]?)
#   _SL_RE      = (?:cắt\s*lỗ|sl|stop)\s*[:=\-]?\s*([\d,.]+[kKmM]?)
#   _TP_HEAD_RE = captures full tail, then split by _TP_SPLIT_PRIMARY_RE / _TP_COMMA_LIST_RE
#   _LEV_RE     = (?:đòn\s*bẩy\s*x?|lev\s*[:=\-]?\s*|x\s*)(\d+)
#
# DEVIATION from _extract_symbol heuristic:
#   channel_b.py._extract_symbol checks the token immediately before/after the
#   side keyword. RegexEngine v1 instead does a simple scan of text.upper() for
#   the first token matching _SYMBOL_RE that is not in skip_symbols.
#   This means fixtures where the symbol appears AFTER a non-skip word (e.g.,
#   "Mua XAUUSD") work fine, but cases where the symbol is ambiguous may differ.
#   Tracked as xfail in test_regex_engine_parity.py; Session 6B will refine.
CHANNEL_B_REGEX_TABLE: dict = {  # type: ignore[type-arg]
    "side": {
        "pattern": r"\b(mua|long|buy|bán|short|sell)\b",
        "flags": ["IGNORECASE", "UNICODE"],
        "group": 1,
    },
    "side_map": {
        "mua": "LONG",
        "long": "LONG",
        "buy": "LONG",
        "bán": "SHORT",
        "short": "SHORT",
        "sell": "SHORT",
    },
    "symbol": {
        # Searched on text.upper() to match channel_b.py's _SYMBOL_RE behavior.
        "pattern": r"\b([A-Z]{2,10}(?:USDT|USD|BTC)?)\b",
        "flags": ["IGNORECASE", "UNICODE"],
        "group": 1,
    },
    "symbol_from_side_group": None,
    "entry": {
        "pattern": r"(?:vùng|entry)\s*[:=\-]?\s*([\d,.]+[kKmM]?)",
        "flags": ["IGNORECASE", "UNICODE"],
        "group": 1,
    },
    "entry_zone": {
        "pattern": r"(?:vùng|entry|zone)\s*[:=\-]?\s*([\d,.]+)\s*[-–—]\s*([\d,.]+)",  # noqa: RUF001
        "flags": ["IGNORECASE", "UNICODE"],
        "group": 1,
    },
    "sl": {
        "pattern": r"(?:cắt\s*lỗ|sl|stop)\s*[:=\-]?\s*([\d,.]+[kKmM]?)",
        "flags": ["IGNORECASE", "UNICODE"],
        "group": 1,
    },
    "tp": {
        # Captures the full TP tail — mirroring _TP_HEAD_RE.
        "pattern": (
            r"(?:mục\s*tiêu|target|tp\d*)\s*[:=\-]?\s*"
            r"([\d,.kKmM]+"
            r"(?:\s*[/,]\s*[\d,.kKmM]+|(?:\s+và\s+|\s+)[\d,.kKmM]+)*)"
        ),
        "flags": ["IGNORECASE", "UNICODE"],
        "group": 1,
    },
    # Comma that is NOT a thousands separator.
    "tp_comma_list": r",(?!\d{3}(?:[^\d]|$))",
    # Split on slash, "và", or whitespace — mirrors _TP_SPLIT_PRIMARY_RE.
    "tp_split": r"/|\bvà\b|\s+",
    "leverage": {
        "pattern": r"(?:đòn\s*bẩy\s*x?|lev\s*[:=\-]?\s*|x\s*)(\d+)",
        "flags": ["IGNORECASE", "UNICODE"],
        "group": 1,
    },
    # Strip emoji / non-word chars but keep dashes, digits, letters, common punct.
    # Mirrors: re.sub(r"[^\w\s–—\-,./:=]", " ", text, flags=re.UNICODE)  # noqa: RUF003
    "pre_clean": r"[^\w\s–—\-,./:=]",  # noqa: RUF001
    "skip_symbols": [
        "MUA", "BAN", "LONG", "SHORT", "BUY", "SELL", "ENTRY", "VUNG",
        "ZONE", "SL", "TP", "TARGET", "AND", "VA", "LEV", "STOP",
    ],
}
