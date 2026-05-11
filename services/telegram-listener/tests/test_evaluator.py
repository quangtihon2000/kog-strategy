"""Tests for induction.evaluator -- match, mismatch, parse_failed, timeout."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from tg_listener.induction.evaluator import EvalReport, evaluate, is_acceptable
from tg_listener.parsers.regex_table import RegexSlot, RegexTable

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_table() -> RegexTable:
    """Build a minimal RegexTable that parses 'LONG XAUUSD Entry N SL N TP N' messages."""
    return RegexTable(
        side=RegexSlot(
            pattern=r"(LONG|SHORT)\s+(\w+)",
            flags=["IGNORECASE", "UNICODE"],
            group=1,
        ),
        side_map={"long": "LONG", "short": "SHORT"},
        symbol=None,
        symbol_from_side_group=2,
        entry=RegexSlot(
            pattern=r"Entry\s*([\d,.]+)",
            flags=["IGNORECASE", "UNICODE"],
            group=1,
        ),
        sl=RegexSlot(
            pattern=r"SL\s*([\d,.]+)",
            flags=["IGNORECASE", "UNICODE"],
            group=1,
        ),
        tp=RegexSlot(
            pattern=r"TP\s*([\d,.]+)",
            flags=["IGNORECASE", "UNICODE"],
            group=1,
        ),
        skip_symbols=[],
    )


def _exp(
    *,
    symbol: str = "XAUUSD",
    side: str = "LONG",
    entry: float = 2350.0,
    sl: float = 2342.0,
    tp: list[float] | None = None,
    leverage: int | None = None,
) -> dict[str, Any]:
    """Build an expected parsed_signal dict."""
    return {
        "symbol": symbol,
        "side": side,
        "entry": entry,
        "sl": sl,
        "tp": tp if tp is not None else [2360.0],
        "leverage": leverage,
    }


def _sample(text: str, expected: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(text=text, parsed_signal=expected)


# ── Test cases ─────────────────────────────────────────────────────────────────


def test_evaluate_all_matched() -> None:
    """All 3 samples parse correctly and match expected output -> match_rate = 1.0."""
    table = _make_table()
    samples = [
        _sample("LONG XAUUSD Entry 2350 SL 2342 TP 2360", _exp()),
        _sample(
            "LONG XAUUSD Entry 2355 SL 2345 TP 2365",
            _exp(entry=2355.0, sl=2345.0, tp=[2365.0]),
        ),
        _sample(
            "SHORT XAUUSD Entry 2300 SL 2315 TP 2290",
            _exp(side="SHORT", entry=2300.0, sl=2315.0, tp=[2290.0]),
        ),
    ]

    report = evaluate(table, samples)

    assert report.total == 3
    assert report.matched == 3
    assert report.mismatched == 0
    assert report.parse_failed == 0
    assert report.timeouts == 0
    assert report.match_rate == pytest.approx(1.0)
    assert is_acceptable(report)


def test_evaluate_partial_match() -> None:
    """2 match, 1 mismatch, 1 parse_failed out of 4 samples."""
    table = _make_table()
    samples = [
        # Matches.
        _sample("LONG XAUUSD Entry 2350 SL 2342 TP 2360", _exp()),
        _sample(
            "SHORT XAUUSD Entry 2300 SL 2315 TP 2290",
            _exp(side="SHORT", entry=2300.0, sl=2315.0, tp=[2290.0]),
        ),
        # Mismatch: wrong expected sl.
        _sample("LONG XAUUSD Entry 2350 SL 2342 TP 2360", _exp(sl=9999.0)),
        # Parse failure: no parseable signal in text.
        _sample("This is just a chat message, no signal here", _exp()),
    ]

    report = evaluate(table, samples)

    assert report.total == 4
    assert report.matched == 2
    assert report.mismatched == 1
    assert report.parse_failed == 1
    assert report.timeouts == 0
    assert report.match_rate == pytest.approx(0.5)
    assert not is_acceptable(report)


def test_evaluate_parse_failed_all() -> None:
    """No sample parses successfully."""
    table = _make_table()
    stub_exp: dict[str, Any] = {
        "symbol": "X",
        "side": "LONG",
        "entry": 1.0,
        "sl": 1.0,
        "tp": [1.0],
        "leverage": None,
    }
    samples = [
        _sample("random text 1", stub_exp),
        _sample("random text 2", stub_exp),
    ]

    report = evaluate(table, samples)

    assert report.total == 2
    assert report.matched == 0
    assert report.parse_failed == 2
    assert report.match_rate == pytest.approx(0.0)


def test_evaluate_mismatch_wrong_side() -> None:
    """Parsed side differs from expected -- counts as mismatch."""
    table = _make_table()
    samples = [
        # Expected SHORT but parsed LONG.
        _sample("LONG XAUUSD Entry 2350 SL 2342 TP 2360", _exp(side="SHORT")),
    ]

    report = evaluate(table, samples)

    assert report.mismatched == 1
    assert report.matched == 0


def test_evaluate_timeout_catastrophic_backtracking() -> None:
    """Catastrophic backtracking regex on crafted input must hit timeout quickly."""
    # (a+)+ on 30x"a" + "X" is classically catastrophic.
    catastrophic_table = RegexTable(
        side=RegexSlot(pattern=r"^(a+)+$", flags=[], group=0),
        side_map={"aaaa": "LONG"},  # won't match -- just needs to be present
        symbol=None,
        symbol_from_side_group=None,
        entry=RegexSlot(
            pattern=r"Entry\s*([\d,.]+)",
            flags=["IGNORECASE"],
            group=1,
        ),
        sl=RegexSlot(
            pattern=r"SL\s*([\d,.]+)",
            flags=["IGNORECASE"],
            group=1,
        ),
        tp=RegexSlot(
            pattern=r"TP\s*([\d,.]+)",
            flags=["IGNORECASE"],
            group=1,
        ),
        skip_symbols=[],
    )
    # Crafted input that triggers catastrophic backtracking.
    evil_text = "a" * 30 + "X"
    samples = [_sample(evil_text, {})]

    # Use a very short timeout so the test stays fast.
    report = evaluate(catastrophic_table, samples, per_text_timeout_s=0.05)

    # Should either timeout or parse_failed -- either way, no match.
    assert report.matched == 0
    assert report.timeouts + report.parse_failed >= 1


def test_is_acceptable_threshold() -> None:
    """is_acceptable respects the threshold parameter."""
    report = EvalReport(total=10, matched=9, mismatched=1, parse_failed=0, timeouts=0)
    assert is_acceptable(report, threshold=0.9)
    assert not is_acceptable(report, threshold=0.95)


def test_eval_report_empty() -> None:
    """Empty sample list -> match_rate = 0.0."""
    table = _make_table()
    report = evaluate(table, [])
    assert report.total == 0
    assert report.match_rate == pytest.approx(0.0)
