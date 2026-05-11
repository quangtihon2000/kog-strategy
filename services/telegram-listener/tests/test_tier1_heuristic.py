"""Tier 1 unit tests — fixture-driven (spec §5.3).

Each fixture in `tests.fixtures.messages.FIXTURES` carries an expected
verdict and (when rejecting) the expected `Tier1Decision.reason` label.
Parametrising over the corpus makes failures point at a single id, so
adding a new edge case is a one-line addition to the fixture file.
"""

from __future__ import annotations

import pytest

from tg_listener.tiers.tier1_heuristic import (
    ANTI_ANYWHERE,
    ANTI_HEAD,
    POSITIVE_PRICE_KW,
    POSITIVE_SIDE,
    Tier1Decision,
    evaluate,
    is_likely_signal,
)

from .fixtures.messages import FIXTURES, Tier1Fixture, non_signals, signals


def test_corpus_has_at_least_30_fixtures() -> None:
    assert len(FIXTURES) >= 30, "Spec §5.3 audit needs ≥30 hand-curated cases"


def test_corpus_has_both_signals_and_non_signals() -> None:
    # Sanity: a one-sided corpus cannot prove the gate works.
    assert len(signals()) >= 10
    assert len(non_signals()) >= 20


def test_corpus_ids_unique() -> None:
    ids = [f.id for f in FIXTURES]
    assert len(ids) == len(set(ids)), "Duplicate fixture ids confuse pytest output"


def test_reject_fixtures_declare_reason() -> None:
    for f in non_signals():
        assert f.reason is not None, f"{f.id}: reject fixtures must declare expected reason"


@pytest.mark.parametrize("fx", FIXTURES, ids=lambda f: f.id)
def test_is_likely_signal_matches_expectation(fx: Tier1Fixture) -> None:
    assert is_likely_signal(fx.text) is fx.expected_pass


@pytest.mark.parametrize("fx", non_signals(), ids=lambda f: f.id)
def test_reject_reason_matches_fixture(fx: Tier1Fixture) -> None:
    decision = evaluate(fx.text)
    assert decision.action == "reject"
    assert decision.reason == fx.reason, (
        f"{fx.id}: expected reason={fx.reason!r}, got {decision.reason!r}"
    )


@pytest.mark.parametrize("fx", signals(), ids=lambda f: f.id)
def test_pass_decision_is_ok(fx: Tier1Fixture) -> None:
    decision = evaluate(fx.text)
    assert decision.action == "pass"
    assert decision.reason == "ok"


def test_decision_is_frozen() -> None:
    d = evaluate("LONG BTCUSDT entry 67500 sl 66800 tp 68500")
    with pytest.raises(Exception):  # pydantic ValidationError on frozen mutate  # noqa: B017
        d.action = "reject"  # type: ignore[misc]


def test_keyword_lists_are_lowercase() -> None:
    # The gate lower()s the message once; keyword tables must already be
    # lowercase or anti-head/anti-anywhere matches will silently miss.
    for kw in (*ANTI_HEAD, *ANTI_ANYWHERE, *POSITIVE_PRICE_KW, *POSITIVE_SIDE):
        assert kw == kw.lower(), f"keyword {kw!r} must be lowercase"


def test_evaluate_returns_tier1_decision_type() -> None:
    assert isinstance(evaluate("ok"), Tier1Decision)
