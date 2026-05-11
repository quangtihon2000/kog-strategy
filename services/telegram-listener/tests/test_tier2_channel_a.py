"""Tier 2 Channel A parser tests — fixture-driven."""

from __future__ import annotations

import pytest

from tg_listener.parsers.channel_a import ChannelAParser

from .fixtures.channel_a import CHANNEL_A_FIXTURES, ChannelAFixture

_parser = ChannelAParser()


def test_corpus_has_enough_fixtures() -> None:
    assert len(CHANNEL_A_FIXTURES) >= 20


def test_corpus_has_valid_and_invalid() -> None:
    valid = [f for f in CHANNEL_A_FIXTURES if f.expected is not None]
    invalid = [f for f in CHANNEL_A_FIXTURES if f.expected is None]
    assert len(valid) >= 12
    assert len(invalid) >= 8


def test_fixture_ids_unique() -> None:
    ids = [f.id for f in CHANNEL_A_FIXTURES]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("fx", CHANNEL_A_FIXTURES, ids=lambda f: f.id)
def test_channel_a_parse(fx: ChannelAFixture) -> None:
    result = _parser.parse(fx.text)
    if fx.expected is None:
        assert result is None, f"{fx.id}: expected None, got {result!r}"
    else:
        assert result is not None, f"{fx.id}: expected signal, got None"
        assert result.symbol == fx.expected.symbol, f"{fx.id}: symbol mismatch"
        assert result.side == fx.expected.side, f"{fx.id}: side mismatch"
        assert result.entry == pytest.approx(fx.expected.entry), f"{fx.id}: entry mismatch"
        assert result.sl == pytest.approx(fx.expected.sl), f"{fx.id}: sl mismatch"
        assert result.tp == pytest.approx(fx.expected.tp), f"{fx.id}: tp mismatch"
        assert result.leverage == fx.expected.leverage, f"{fx.id}: leverage mismatch"
