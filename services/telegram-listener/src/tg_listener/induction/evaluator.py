"""Evaluator — assesses how well a RegexTable matches a set of ParserSamples.

Pure function: no I/O, no DB calls.
Uses ThreadPoolExecutor to guard against catastrophic backtracking.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from typing import Any

from tg_listener.parsers.regex_engine import parse
from tg_listener.parsers.regex_table import RegexTable

# Float tolerance for numeric field comparison.
_FLOAT_EPS = 1e-6


def _float_eq(a: float, b: float) -> bool:
    """Compare two floats with relative tolerance."""
    return abs(a - b) <= _FLOAT_EPS * max(1.0, abs(a))


def _normalize_entry(v: Any) -> Any:
    """Normalise entry: list → sorted tuple so comparison is order-independent."""
    if isinstance(v, list):
        return tuple(sorted(v))
    if isinstance(v, tuple):
        return tuple(sorted(v))
    return v


def _normalize_tp(v: Any) -> list[float]:
    """Normalise tp to a sorted list of floats."""
    if isinstance(v, (list, tuple)):
        return sorted(float(x) for x in v)
    return [float(v)]


def _fields_match(parsed_signal: dict[str, Any], expected: dict[str, Any]) -> bool:
    """Check whether the parsed output matches the expected parsed_signal dict.

    Fields compared: symbol, side, entry, sl, tp (sorted), leverage.
    Numeric fields use _float_eq tolerance.
    """
    # symbol — case-insensitive string comparison.
    if parsed_signal.get("symbol", "").upper() != str(expected.get("symbol", "")).upper():
        return False

    # side — exact string comparison.
    if parsed_signal.get("side") != expected.get("side"):
        return False

    # entry — may be float or [float, float] zone.
    p_entry = _normalize_entry(parsed_signal.get("entry"))
    e_entry = _normalize_entry(expected.get("entry"))

    if type(p_entry) != type(e_entry):  # noqa: E721
        # float vs tuple — not a match.
        return False

    if isinstance(p_entry, tuple):
        if len(p_entry) != len(e_entry):
            return False
        if not all(_float_eq(a, b) for a, b in zip(p_entry, e_entry, strict=True)):
            return False
    else:
        # scalar float
        try:
            if not _float_eq(float(p_entry), float(e_entry)):
                return False
        except (TypeError, ValueError):
            return False

    # sl — scalar float.
    try:
        if not _float_eq(float(parsed_signal.get("sl", 0.0)), float(expected.get("sl", 0.0))):
            return False
    except (TypeError, ValueError):
        return False

    # tp — sorted list of floats.
    p_tp = _normalize_tp(parsed_signal.get("tp", []))
    e_tp = _normalize_tp(expected.get("tp", []))
    if len(p_tp) != len(e_tp):
        return False
    if not all(_float_eq(a, b) for a, b in zip(p_tp, e_tp, strict=True)):
        return False

    # leverage — None or int; allow None == None.
    p_lev = parsed_signal.get("leverage")
    e_lev = expected.get("leverage")
    if p_lev != e_lev:
        return False

    return True


# ── EvalReport ────────────────────────────────────────────────────────────────


@dataclass
class EvalReport:
    """Summary of evaluating a RegexTable against a sample corpus."""

    total: int
    matched: int
    mismatched: int
    parse_failed: int
    timeouts: int

    @property
    def match_rate(self) -> float:
        """Fraction of samples that parsed AND matched expected output."""
        if self.total == 0:
            return 0.0
        return self.matched / self.total

    def __str__(self) -> str:
        return (
            f"EvalReport(total={self.total}, matched={self.matched}, "
            f"mismatched={self.mismatched}, parse_failed={self.parse_failed}, "
            f"timeouts={self.timeouts}, match_rate={self.match_rate:.3f})"
        )


def is_acceptable(report: EvalReport, threshold: float = 0.95) -> bool:
    """Return True if the match_rate meets or exceeds the threshold."""
    return report.match_rate >= threshold


# ── Core evaluation ───────────────────────────────────────────────────────────


def _evaluate_core(
    table: RegexTable,
    samples: list[Any],
    *,
    per_text_timeout_s: float = 0.2,
    collect_disagreements: bool = False,
    max_disagreements: int = 20,
) -> tuple[EvalReport, list[dict[str, Any]]]:
    """Core evaluation logic shared by evaluate() and evaluate_detailed().

    Args:
        table: The RegexTable to evaluate.
        samples: list of objects with .text (str) and .parsed_signal (dict).
        per_text_timeout_s: wall-clock timeout per sample (catastrophic-backtracking guard).
        collect_disagreements: nếu True, thu thập chi tiết từng sample không match.
        max_disagreements: số lượng disagreement record tối đa (chỉ dùng khi collect=True).

    Returns:
        Tuple (EvalReport, list_of_disagreement_dicts).
        Khi collect_disagreements=False, list luôn rỗng.
    """
    total = len(samples)
    matched = 0
    mismatched = 0
    parse_failed = 0
    timeouts = 0
    disagreements: list[dict[str, Any]] = []

    # Dùng một executor chung cho toàn bộ batch để tránh spawn nhiều thread.
    with ThreadPoolExecutor(max_workers=1) as executor:
        for sample in samples:
            sample_id: int | None = getattr(sample, "id", None)
            raw_signal = sample.parsed_signal
            expected: dict[str, Any] = raw_signal if isinstance(raw_signal, dict) else {}

            future = executor.submit(parse, table, sample.text)
            try:
                result = future.result(timeout=per_text_timeout_s)
            except FuturesTimeoutError:
                timeouts += 1
                if collect_disagreements and len(disagreements) < max_disagreements:
                    disagreements.append(
                        {
                            "sample_id": sample_id,
                            "kind": "timeout",
                            "parsed": None,
                            "expected": expected,
                        }
                    )
                continue
            except Exception:
                parse_failed += 1
                if collect_disagreements and len(disagreements) < max_disagreements:
                    disagreements.append(
                        {
                            "sample_id": sample_id,
                            "kind": "parse_failed",
                            "parsed": None,
                            "expected": expected,
                        }
                    )
                continue

            if result is None:
                parse_failed += 1
                if collect_disagreements and len(disagreements) < max_disagreements:
                    disagreements.append(
                        {
                            "sample_id": sample_id,
                            "kind": "parse_failed",
                            "parsed": None,
                            "expected": expected,
                        }
                    )
                continue

            # Convert ParsedSignalFields to dict for comparison.
            parsed_dict = result.model_dump(mode="json")

            if _fields_match(parsed_dict, expected):
                matched += 1
            else:
                mismatched += 1
                if collect_disagreements and len(disagreements) < max_disagreements:
                    disagreements.append(
                        {
                            "sample_id": sample_id,
                            "kind": "mismatch",
                            "parsed": parsed_dict,
                            "expected": expected,
                        }
                    )

    report = EvalReport(
        total=total,
        matched=matched,
        mismatched=mismatched,
        parse_failed=parse_failed,
        timeouts=timeouts,
    )
    return report, disagreements


def evaluate(
    table: RegexTable,
    samples: list[Any],
    *,
    per_text_timeout_s: float = 0.2,
) -> EvalReport:
    """Evaluate a RegexTable against a list of ParserSample-like objects.

    Args:
        table: The RegexTable to evaluate.
        samples: list of objects with .text (str) and .parsed_signal (dict).
        per_text_timeout_s: wall-clock timeout per sample (catastrophic-backtracking guard).

    Returns:
        EvalReport with counts for each outcome category.
    """
    report, _ = _evaluate_core(
        table,
        samples,
        per_text_timeout_s=per_text_timeout_s,
        collect_disagreements=False,
    )
    return report


def evaluate_detailed(
    table: RegexTable,
    samples: list[Any],
    *,
    per_text_timeout_s: float = 0.2,
    max_disagreements: int = 20,
) -> tuple[EvalReport, list[dict[str, Any]]]:
    """Evaluate a RegexTable and also return per-sample disagreement records.

    Args:
        table: The RegexTable to evaluate.
        samples: list of objects with .text (str) and .parsed_signal (dict).
        per_text_timeout_s: wall-clock timeout per sample (catastrophic-backtracking guard).
        max_disagreements: cap on the number of disagreement records returned.

    Returns:
        Tuple of (EvalReport, disagreements).
        Each disagreement dict has keys: sample_id, kind, parsed, expected.
        kind is one of: "mismatch" | "parse_failed" | "timeout".
    """
    return _evaluate_core(
        table,
        samples,
        per_text_timeout_s=per_text_timeout_s,
        collect_disagreements=True,
        max_disagreements=max_disagreements,
    )
