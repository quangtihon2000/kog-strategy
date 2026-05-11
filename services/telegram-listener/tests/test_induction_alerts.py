"""Tests for induction.evaluator.evaluate_detailed and induction.alerts.

Non-DB tests — run without DATABASE_URL.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest

from tg_listener.induction.alerts import maybe_emit_low_match_rate
from tg_listener.induction.evaluator import (
    EvalReport,
    evaluate,
    evaluate_detailed,
)
from tg_listener.parsers.regex_table import RegexSlot, RegexTable

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_table() -> RegexTable:
    """Build a minimal RegexTable that parses 'LONG XAUUSD Entry N SL N TP N'."""
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


def _make_catastrophic_table() -> RegexTable:
    """Build a RegexTable with catastrophic-backtracking pattern for timeout testing."""
    return RegexTable(
        side=RegexSlot(pattern=r"^(a+)+$", flags=[], group=0),
        side_map={"aaaa": "LONG"},
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


def _exp(
    *,
    symbol: str = "XAUUSD",
    side: str = "LONG",
    entry: float = 2350.0,
    sl: float = 2342.0,
    tp: list[float] | None = None,
    leverage: int | None = None,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "side": side,
        "entry": entry,
        "sl": sl,
        "tp": tp if tp is not None else [2360.0],
        "leverage": leverage,
    }


def _sample(
    text: str,
    expected: dict[str, Any],
    sample_id: int | None = None,
) -> SimpleNamespace:
    ns = SimpleNamespace(text=text, parsed_signal=expected)
    if sample_id is not None:
        ns.id = sample_id
    return ns


# ── Tests: evaluate_detailed parity with evaluate() ───────────────────────────


def test_evaluate_detailed_same_counts_as_evaluate() -> None:
    """evaluate_detailed returns identical counts to evaluate() on same samples."""
    table = _make_table()
    samples = [
        _sample("LONG XAUUSD Entry 2350 SL 2342 TP 2360", _exp()),
        _sample("LONG XAUUSD Entry 2350 SL 2342 TP 2360", _exp(sl=9999.0)),  # mismatch
        _sample("not a signal", _exp()),  # parse_failed
    ]

    report_basic = evaluate(table, samples)
    report_det, disagreements = evaluate_detailed(table, samples)

    assert report_det.total == report_basic.total
    assert report_det.matched == report_basic.matched
    assert report_det.mismatched == report_basic.mismatched
    assert report_det.parse_failed == report_basic.parse_failed
    assert report_det.timeouts == report_basic.timeouts
    assert report_det.match_rate == pytest.approx(report_basic.match_rate)
    # Disagreements phải có đúng số lượng không-match.
    expected_dis = report_basic.mismatched + report_basic.parse_failed + report_basic.timeouts
    assert len(disagreements) == expected_dis


def test_evaluate_detailed_mismatch_records() -> None:
    """Disagreement records for mismatch have correct kind and parsed != expected."""
    table = _make_table()
    samples = [
        _sample("LONG XAUUSD Entry 2350 SL 2342 TP 2360", _exp(sl=9999.0), sample_id=42),
    ]

    _, disagreements = evaluate_detailed(table, samples)

    assert len(disagreements) == 1
    rec = disagreements[0]
    assert rec["kind"] == "mismatch"
    assert rec["sample_id"] == 42
    assert rec["parsed"] is not None
    assert rec["expected"]["sl"] == 9999.0


def test_evaluate_detailed_parse_failed_record() -> None:
    """Disagreement record for unparseable text has kind=parse_failed, parsed=None."""
    table = _make_table()
    samples = [
        _sample("totally random chat message", _exp(), sample_id=7),
    ]

    _, disagreements = evaluate_detailed(table, samples)

    assert len(disagreements) == 1
    rec = disagreements[0]
    assert rec["kind"] == "parse_failed"
    assert rec["sample_id"] == 7
    assert rec["parsed"] is None


def test_evaluate_detailed_respects_max_disagreements_cap() -> None:
    """max_disagreements limits the number of disagreement records returned."""
    table = _make_table()
    # 10 mismatch samples.
    samples = [
        _sample("LONG XAUUSD Entry 2350 SL 2342 TP 2360", _exp(sl=9999.0))
        for _ in range(10)
    ]

    _, disagreements = evaluate_detailed(table, samples, max_disagreements=3)

    assert len(disagreements) == 3


def test_evaluate_detailed_sample_id_none_when_no_id_attr() -> None:
    """Samples without .id attribute yield sample_id=None in disagreement records."""
    table = _make_table()
    # SimpleNamespace without id attr — getattr returns None.
    samples = [SimpleNamespace(text="no signal here", parsed_signal=_exp())]

    _, disagreements = evaluate_detailed(table, samples)

    assert disagreements[0]["sample_id"] is None


def test_evaluate_detailed_timeout_record() -> None:
    """Catastrophic-backtracking regex triggers timeout, kind=timeout, parsed=None."""
    catastrophic_table = _make_catastrophic_table()
    evil_text = "a" * 30 + "X"
    samples = [_sample(evil_text, {}, sample_id=99)]

    _, disagreements = evaluate_detailed(
        catastrophic_table, samples, per_text_timeout_s=0.05
    )

    assert len(disagreements) >= 1
    rec = disagreements[0]
    assert rec["kind"] in ("timeout", "parse_failed")
    assert rec["parsed"] is None
    assert rec["sample_id"] == 99


def test_evaluate_detailed_no_disagreements_on_all_match() -> None:
    """No disagreement records when all samples match."""
    table = _make_table()
    samples = [
        _sample("LONG XAUUSD Entry 2350 SL 2342 TP 2360", _exp()),
        _sample(
            "SHORT XAUUSD Entry 2300 SL 2315 TP 2290",
            _exp(side="SHORT", entry=2300.0, sl=2315.0, tp=[2290.0]),
        ),
    ]

    _, disagreements = evaluate_detailed(table, samples)

    assert disagreements == []


# ── Tests: maybe_emit_low_match_rate ──────────────────────────────────────────


def _make_report(
    total: int,
    matched: int,
    mismatched: int = 0,
    parse_failed: int = 0,
    timeouts: int = 0,
) -> EvalReport:
    return EvalReport(
        total=total,
        matched=matched,
        mismatched=mismatched,
        parse_failed=parse_failed,
        timeouts=timeouts,
    )


def test_alert_emits_warning_below_threshold(caplog: pytest.LogCaptureFixture) -> None:
    """Emits WARNING when match_rate < threshold and total > 0."""
    report = _make_report(total=10, matched=8, mismatched=2)
    with caplog.at_level(logging.WARNING, logger="tg_listener.induction.alerts"):
        maybe_emit_low_match_rate(
            channel_id=123,
            parser_id=456,
            report=report,
            threshold=0.95,
        )
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.WARNING


def test_alert_silent_at_or_above_threshold(caplog: pytest.LogCaptureFixture) -> None:
    """No log emitted when match_rate >= threshold."""
    report = _make_report(total=20, matched=20)
    with caplog.at_level(logging.WARNING, logger="tg_listener.induction.alerts"):
        maybe_emit_low_match_rate(
            channel_id=123,
            parser_id=1,
            report=report,
            threshold=0.95,
        )
    assert len(caplog.records) == 0


def test_alert_silent_at_exact_threshold(caplog: pytest.LogCaptureFixture) -> None:
    """No log emitted when match_rate exactly equals threshold."""
    report = _make_report(total=20, matched=19, mismatched=1)
    # match_rate = 19/20 = 0.95
    with caplog.at_level(logging.WARNING, logger="tg_listener.induction.alerts"):
        maybe_emit_low_match_rate(
            channel_id=123,
            parser_id=1,
            report=report,
            threshold=0.95,
        )
    assert len(caplog.records) == 0


def test_alert_silent_when_total_zero(caplog: pytest.LogCaptureFixture) -> None:
    """No log emitted when total=0 (no samples evaluated)."""
    report = _make_report(total=0, matched=0)
    with caplog.at_level(logging.WARNING, logger="tg_listener.induction.alerts"):
        maybe_emit_low_match_rate(
            channel_id=123,
            parser_id=None,
            report=report,
            threshold=0.95,
        )
    assert len(caplog.records) == 0


def test_alert_extra_fields_present(caplog: pytest.LogCaptureFixture) -> None:
    """Log record extra fields include event=low_match_rate and all metric keys."""
    report = _make_report(total=10, matched=5, mismatched=3, parse_failed=1, timeouts=1)
    with caplog.at_level(logging.WARNING, logger="tg_listener.induction.alerts"):
        maybe_emit_low_match_rate(
            channel_id=777,
            parser_id=42,
            report=report,
            threshold=0.95,
        )
    assert len(caplog.records) == 1
    rec = caplog.records[-1]
    assert rec.event == "low_match_rate"  # type: ignore[attr-defined]
    assert rec.channel_id == 777  # type: ignore[attr-defined]
    assert rec.parser_id == 42  # type: ignore[attr-defined]
    assert rec.match_rate == pytest.approx(0.5)  # type: ignore[attr-defined]
    assert rec.total == 10  # type: ignore[attr-defined]
    assert rec.mismatched == 3  # type: ignore[attr-defined]
    assert rec.parse_failed == 1  # type: ignore[attr-defined]
    assert rec.timeouts == 1  # type: ignore[attr-defined]
    assert rec.threshold == pytest.approx(0.95)  # type: ignore[attr-defined]


def test_alert_parser_id_none_allowed(caplog: pytest.LogCaptureFixture) -> None:
    """parser_id=None is valid (not_acceptable branch where no parser was created)."""
    report = _make_report(total=5, matched=1, mismatched=4)
    with caplog.at_level(logging.WARNING, logger="tg_listener.induction.alerts"):
        maybe_emit_low_match_rate(
            channel_id=100,
            parser_id=None,
            report=report,
            threshold=0.95,
        )
    assert len(caplog.records) == 1
    assert caplog.records[-1].parser_id is None  # type: ignore[attr-defined]
