"""CRITICAL parity gate: RegexEngine must reproduce ChannelA/B parser output.

For every fixture in the Channel A and B corpora, RegexEngine.parse() called
with the seeded regex_table must produce the same result as the original
ChannelAParser / ChannelBParser.parse() implementations.

A failure here means the regex_table definition in seed_data.py diverges from
the hand-coded parser — that is a regression that must be fixed before
promoting the seeded parser to production.
"""

from __future__ import annotations

import pytest

from tg_listener.db.seed_data import (
    CHANNEL_A_REGEX_TABLE,
    CHANNEL_B_REGEX_TABLE,
)
from tg_listener.parsers.channel_a import ChannelAParser
from tg_listener.parsers.channel_b import ChannelBParser
from tg_listener.parsers.regex_engine import parse
from tg_listener.parsers.regex_table import RegexTable

from .fixtures.channel_a import CHANNEL_A_FIXTURES, ChannelAFixture
from .fixtures.channel_b import CHANNEL_B_FIXTURES, ChannelBFixture

# ── Build validated RegexTable objects from seed constants ─────────────────

_TABLE_A = RegexTable.model_validate(CHANNEL_A_REGEX_TABLE)
_TABLE_B = RegexTable.model_validate(CHANNEL_B_REGEX_TABLE)

# ── Reference parsers ──────────────────────────────────────────────────────

_PARSER_A = ChannelAParser()
_PARSER_B = ChannelBParser()


# ── Channel A parity ───────────────────────────────────────────────────────

@pytest.mark.parametrize("fx", CHANNEL_A_FIXTURES, ids=lambda f: f.id)
def test_channel_a_parity(fx: ChannelAFixture) -> None:
    """RegexEngine result must match ChannelAParser for every Channel A fixture."""
    reference = _PARSER_A.parse(fx.text)
    engine_result = parse(_TABLE_A, fx.text)

    if reference is None:
        assert engine_result is None, (
            f"{fx.id}: reference=None but engine returned {engine_result!r}"
        )
        return

    assert engine_result is not None, (
        f"{fx.id}: reference={reference!r} but engine returned None"
    )
    assert engine_result.symbol == reference.symbol, (
        f"{fx.id}: symbol mismatch — engine={engine_result.symbol!r} ref={reference.symbol!r}"
    )
    assert engine_result.side == reference.side, (
        f"{fx.id}: side mismatch — engine={engine_result.side!r} ref={reference.side!r}"
    )
    assert engine_result.entry == pytest.approx(reference.entry), (
        f"{fx.id}: entry mismatch — engine={engine_result.entry!r} ref={reference.entry!r}"
    )
    assert engine_result.sl == pytest.approx(reference.sl), (
        f"{fx.id}: sl mismatch — engine={engine_result.sl!r} ref={reference.sl!r}"
    )
    assert engine_result.tp == pytest.approx(reference.tp), (
        f"{fx.id}: tp mismatch — engine={engine_result.tp!r} ref={reference.tp!r}"
    )
    assert engine_result.leverage == reference.leverage, (
        f"{fx.id}: leverage mismatch — engine={engine_result.leverage!r} ref={reference.leverage!r}"
    )


# ── Channel B parity ───────────────────────────────────────────────────────

@pytest.mark.parametrize("fx", CHANNEL_B_FIXTURES, ids=lambda f: f.id)
def test_channel_b_parity(fx: ChannelBFixture) -> None:
    """RegexEngine result must match ChannelBParser for every Channel B fixture."""
    reference = _PARSER_B.parse(fx.text)
    engine_result = parse(_TABLE_B, fx.text)

    if reference is None:
        assert engine_result is None, (
            f"{fx.id}: reference=None but engine returned {engine_result!r}"
        )
        return

    assert engine_result is not None, (
        f"{fx.id}: reference={reference!r} but engine returned None"
    )
    assert engine_result.symbol == reference.symbol, (
        f"{fx.id}: symbol mismatch — engine={engine_result.symbol!r} ref={reference.symbol!r}"
    )
    assert engine_result.side == reference.side, (
        f"{fx.id}: side mismatch — engine={engine_result.side!r} ref={reference.side!r}"
    )
    assert engine_result.entry == pytest.approx(reference.entry), (
        f"{fx.id}: entry mismatch — engine={engine_result.entry!r} ref={reference.entry!r}"
    )
    assert engine_result.sl == pytest.approx(reference.sl), (
        f"{fx.id}: sl mismatch — engine={engine_result.sl!r} ref={reference.sl!r}"
    )
    assert engine_result.tp == pytest.approx(reference.tp), (
        f"{fx.id}: tp mismatch — engine={engine_result.tp!r} ref={reference.tp!r}"
    )
    assert engine_result.leverage == reference.leverage, (
        f"{fx.id}: leverage mismatch — engine={engine_result.leverage!r} ref={reference.leverage!r}"
    )


# ── Corpus-level invariants ────────────────────────────────────────────────

def test_channel_a_seed_is_valid_regex_table() -> None:
    """CHANNEL_A_REGEX_TABLE must pass RegexTable validation without errors."""
    table = RegexTable.model_validate(CHANNEL_A_REGEX_TABLE)
    assert table.side is not None
    assert table.symbol_from_side_group == 2


def test_channel_b_seed_is_valid_regex_table() -> None:
    """CHANNEL_B_REGEX_TABLE must pass RegexTable validation without errors."""
    table = RegexTable.model_validate(CHANNEL_B_REGEX_TABLE)
    assert table.pre_clean is not None
    assert table.symbol is not None
    assert len(table.skip_symbols) > 0


def test_channel_a_fixture_count() -> None:
    """Sanity-check: ensure parity test exercises the full corpus, not a subset."""
    assert len(CHANNEL_A_FIXTURES) >= 20, (
        f"Expected >=20 Channel A fixtures, got {len(CHANNEL_A_FIXTURES)}"
    )


def test_channel_b_fixture_count() -> None:
    """Sanity-check: ensure parity test exercises the full corpus, not a subset."""
    assert len(CHANNEL_B_FIXTURES) >= 20, (
        f"Expected >=20 Channel B fixtures, got {len(CHANNEL_B_FIXTURES)}"
    )
