"""Unit tests for RegexEngine.parse() using hand-crafted regex_tables.

These tests exercise the engine's algorithm independently of any DB state.
"""

from __future__ import annotations

import pytest

from tg_listener.parsers.regex_engine import parse
from tg_listener.parsers.regex_table import RegexTable

# ── Minimal valid tables ───────────────────────────────────────────────────

_SIMPLE_TABLE = RegexTable.model_validate(
    {
        "side": {"pattern": r"(LONG|SHORT)\s+(\w+)", "flags": ["IGNORECASE", "UNICODE"], "group": 1},  # noqa: E501
        "side_map": {"long": "LONG", "short": "SHORT"},
        "symbol_from_side_group": 2,
        "entry": {"pattern": r"Entry[:=\s]+([\d,.]+)", "flags": ["IGNORECASE", "UNICODE"], "group": 1},  # noqa: E501
        "sl": {"pattern": r"SL[:=\s]+([\d,.]+)", "flags": ["IGNORECASE", "UNICODE"], "group": 1},
        "tp": {"pattern": r"TP[:=\s]+([\d,.]+)", "flags": ["IGNORECASE", "UNICODE"], "group": 1},
    }
)

_ZONE_TABLE = RegexTable.model_validate(
    {
        "side": {"pattern": r"(LONG|SHORT)\s+(\w+)", "flags": ["IGNORECASE", "UNICODE"], "group": 1},  # noqa: E501
        "side_map": {"long": "LONG", "short": "SHORT"},
        "symbol_from_side_group": 2,
        "entry": {"pattern": r"Entry[:=\s]+([\d,.]+)", "flags": ["IGNORECASE", "UNICODE"], "group": 1},  # noqa: E501
        "entry_zone": {
            "pattern": r"Entry\s*zone\s+([\d,.]+)[-–—]([\d,.]+)",  # noqa: RUF001
            "flags": ["IGNORECASE", "UNICODE"],
            "group": 1,
        },
        "sl": {"pattern": r"SL[:=\s]+([\d,.]+)", "flags": ["IGNORECASE", "UNICODE"], "group": 1},
        "tp": {"pattern": r"TP[:=\s]+([\d,.]+)", "flags": ["IGNORECASE", "UNICODE"], "group": 1},
    }
)


# ── Basic parsing ──────────────────────────────────────────────────────────

def test_parse_basic_long() -> None:
    text = "LONG BTC Entry: 100 SL: 90 TP: 110"
    result = parse(_SIMPLE_TABLE, text)
    assert result is not None
    assert result.symbol == "BTC"
    assert result.side == "LONG"
    assert result.entry == pytest.approx(100.0)
    assert result.sl == pytest.approx(90.0)
    assert result.tp == pytest.approx([110.0])


def test_parse_basic_short() -> None:
    text = "SHORT ETH Entry: 3500 SL: 3600 TP: 3400"
    result = parse(_SIMPLE_TABLE, text)
    assert result is not None
    assert result.side == "SHORT"


def test_parse_missing_side_returns_none() -> None:
    text = "Entry: 100 SL: 90 TP: 110"
    assert parse(_SIMPLE_TABLE, text) is None


def test_parse_missing_entry_returns_none() -> None:
    text = "LONG BTC SL: 90 TP: 110"
    assert parse(_SIMPLE_TABLE, text) is None


def test_parse_missing_sl_returns_none() -> None:
    text = "LONG BTC Entry: 100 TP: 110"
    assert parse(_SIMPLE_TABLE, text) is None


def test_parse_missing_tp_returns_none() -> None:
    text = "LONG BTC Entry: 100 SL: 90"
    assert parse(_SIMPLE_TABLE, text) is None


# ── Entry zone ─────────────────────────────────────────────────────────────

def test_parse_entry_zone() -> None:
    text = "LONG BTC Entry zone 95-105 SL: 90 TP: 115"
    result = parse(_ZONE_TABLE, text)
    assert result is not None
    assert result.entry == pytest.approx((95.0, 105.0))


def test_parse_falls_back_to_single_entry_when_no_zone() -> None:
    text = "LONG BTC Entry: 100 SL: 90 TP: 115"
    result = parse(_ZONE_TABLE, text)
    assert result is not None
    assert result.entry == pytest.approx(100.0)


# ── TP splitting ───────────────────────────────────────────────────────────

def test_parse_multiple_tps() -> None:
    table = RegexTable.model_validate(
        {
            "side": {"pattern": r"(LONG|SHORT)\s+(\w+)", "group": 1},
            "side_map": {"long": "LONG", "short": "SHORT"},
            "symbol_from_side_group": 2,
            "entry": {"pattern": r"Entry:\s*([\d,.]+)", "group": 1},
            "sl": {"pattern": r"SL:\s*([\d,.]+)", "group": 1},
            "tp": {"pattern": r"TP\d*:\s*([\d,.]+)", "group": 1},
        }
    )
    text = "LONG XAU Entry: 2350 SL: 2340 TP1: 2360 TP2: 2370 TP3: 2380"
    result = parse(table, text)
    assert result is not None
    assert result.tp == pytest.approx([2360.0, 2370.0, 2380.0])


def test_parse_tp_with_split() -> None:
    """tp_split breaks a multi-value TP tail into individual floats."""
    table = RegexTable.model_validate(
        {
            "side": {"pattern": r"(LONG|SHORT)\s+(\w+)", "group": 1},
            "side_map": {"long": "LONG", "short": "SHORT"},
            "symbol_from_side_group": 2,
            "entry": {"pattern": r"entry\s+([\d]+)", "group": 1},
            "sl": {"pattern": r"sl\s+([\d]+)", "group": 1},
            "tp": {
                "pattern": r"tp\s+([\d]+(?:[/,]\s*[\d]+)*)",
                "group": 1,
            },
            "tp_split": r"[/,]",
        }
    )
    text = "LONG BTC entry 100 sl 90 tp 110/115/120"
    result = parse(table, text)
    assert result is not None
    assert result.tp == pytest.approx([110.0, 115.0, 120.0])


# ── Leverage ───────────────────────────────────────────────────────────────

def test_parse_leverage() -> None:
    table = RegexTable.model_validate(
        {
            "side": {"pattern": r"(LONG|SHORT)\s+(\w+)", "group": 1},
            "side_map": {"long": "LONG", "short": "SHORT"},
            "symbol_from_side_group": 2,
            "entry": {"pattern": r"Entry:\s*([\d,.]+)", "group": 1},
            "sl": {"pattern": r"SL:\s*([\d,.]+)", "group": 1},
            "tp": {"pattern": r"TP:\s*([\d,.]+)", "group": 1},
            "leverage": {"pattern": r"Lev:\s*(\d+)", "group": 1},
        }
    )
    text = "LONG BTC Entry: 100 SL: 90 TP: 110 Lev: 10"
    result = parse(table, text)
    assert result is not None
    assert result.leverage == 10


def test_parse_leverage_optional_when_missing() -> None:
    table = RegexTable.model_validate(
        {
            "side": {"pattern": r"(LONG|SHORT)\s+(\w+)", "group": 1},
            "side_map": {"long": "LONG", "short": "SHORT"},
            "symbol_from_side_group": 2,
            "entry": {"pattern": r"Entry:\s*([\d,.]+)", "group": 1},
            "sl": {"pattern": r"SL:\s*([\d,.]+)", "group": 1},
            "tp": {"pattern": r"TP:\s*([\d,.]+)", "group": 1},
            "leverage": {"pattern": r"Lev:\s*(\d+)", "group": 1},
        }
    )
    text = "LONG BTC Entry: 100 SL: 90 TP: 110"
    result = parse(table, text)
    assert result is not None
    assert result.leverage is None


# ── pre_clean ──────────────────────────────────────────────────────────────

def test_parse_pre_clean_strips_emoji() -> None:
    """pre_clean replaces emoji with spaces so patterns can match."""
    table = RegexTable.model_validate(
        {
            "side": {"pattern": r"\b(long|short)\b", "group": 1},
            "side_map": {"long": "LONG", "short": "SHORT"},
            "symbol": {
                "pattern": r"\b([A-Z]{2,10})\b",
                "group": 1,
            },
            "skip_symbols": ["LONG", "SHORT", "ENTRY", "SL", "TP"],
            "entry": {"pattern": r"entry\s+([\d]+)", "group": 1},
            "sl": {"pattern": r"sl\s+([\d]+)", "group": 1},
            "tp": {"pattern": r"tp\s+([\d]+)", "group": 1},
            "pre_clean": r"[^\w\s\-,./]",
        }
    )
    text = "🟢 long BTC 🟢 entry 100 sl 90 tp 110"
    result = parse(table, text)
    assert result is not None
    assert result.side == "LONG"


# ── skip_symbols ───────────────────────────────────────────────────────────

def test_parse_skip_symbols() -> None:
    """Tokens in skip_symbols must not be picked up as the symbol."""
    table = RegexTable.model_validate(
        {
            "side": {"pattern": r"\b(long|short)\b", "group": 1},
            "side_map": {"long": "LONG", "short": "SHORT"},
            "symbol": {
                "pattern": r"\b([A-Z]{2,10})\b",
                "group": 1,
            },
            "skip_symbols": ["LONG", "SHORT"],
            "entry": {"pattern": r"entry\s+([\d]+)", "group": 1},
            "sl": {"pattern": r"sl\s+([\d]+)", "group": 1},
            "tp": {"pattern": r"tp\s+([\d]+)", "group": 1},
        }
    )
    text = "long BTC entry 100 sl 90 tp 110"
    result = parse(table, text)
    assert result is not None
    assert result.symbol == "BTC"


# ── k/M number suffixes ────────────────────────────────────────────────────

def test_parse_k_suffix() -> None:
    """k/M suffixes are normalised by _norm_num when the pattern captures them."""
    table = RegexTable.model_validate(
        {
            "side": {"pattern": r"(LONG|SHORT)\s+(\w+)", "flags": ["IGNORECASE", "UNICODE"], "group": 1},  # noqa: E501
            "side_map": {"long": "LONG", "short": "SHORT"},
            "symbol_from_side_group": 2,
            "entry": {"pattern": r"Entry[:=\s]+([\d,.]+[kKmM]?)", "flags": ["IGNORECASE", "UNICODE"], "group": 1},  # noqa: E501
            "sl": {"pattern": r"SL[:=\s]+([\d,.]+[kKmM]?)", "flags": ["IGNORECASE", "UNICODE"], "group": 1},  # noqa: E501
            "tp": {"pattern": r"TP[:=\s]+([\d,.]+[kKmM]?)", "flags": ["IGNORECASE", "UNICODE"], "group": 1},  # noqa: E501
        }
    )
    text = "LONG BTC Entry: 67.5k SL: 66.8k TP: 68.2k"
    result = parse(table, text)
    assert result is not None
    assert result.entry == pytest.approx(67500.0)
    assert result.sl == pytest.approx(66800.0)
    assert result.tp == pytest.approx([68200.0])


# ── Confidence default ─────────────────────────────────────────────────────

def test_parse_confidence_defaults_to_1() -> None:
    text = "LONG BTC Entry: 100 SL: 90 TP: 110"
    result = parse(_SIMPLE_TABLE, text)
    assert result is not None
    assert result.confidence == pytest.approx(1.0)
