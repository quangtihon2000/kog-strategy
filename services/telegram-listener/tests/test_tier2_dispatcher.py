"""Tier 2 dispatcher tests — registration, dispatch, error swallowing."""

from __future__ import annotations

import pytest

from tg_listener.models import ParsedSignalFields
from tg_listener.parsers.channel_a import CHANNEL_A_ID
from tg_listener.parsers.channel_b import CHANNEL_B_ID
from tg_listener.parsers.dispatcher import PARSERS, parse_tier2
from tg_listener.tiers.tier2_regex import parse_tier2 as tier2_re_export


def test_unknown_channel_returns_none() -> None:
    assert parse_tier2(channel_id=0, text="LONG BTCUSDT Entry: 67500 SL: 66800 TP1: 68200") is None


def test_channel_a_registered() -> None:
    assert CHANNEL_A_ID in PARSERS


def test_channel_b_registered() -> None:
    assert CHANNEL_B_ID in PARSERS


def test_channel_a_parses_valid_signal() -> None:
    text = "LONG XAUUSD\nEntry: 2350\nSL: 2342\nTP1: 2362\nTP2: 2370"
    result = parse_tier2(CHANNEL_A_ID, text)
    assert result is not None
    assert result.symbol == "XAUUSD"
    assert result.side == "LONG"
    assert result.entry == pytest.approx(2350.0)
    assert result.sl == pytest.approx(2342.0)
    assert result.tp == pytest.approx([2362.0, 2370.0])


def test_channel_a_returns_none_for_invalid() -> None:
    result = parse_tier2(CHANNEL_A_ID, "Hello everyone, how are you?")
    assert result is None


def test_channel_b_parses_valid_signal() -> None:
    text = "Mua BTC vùng 67400 cắt lỗ 66900 mục tiêu 68200"
    result = parse_tier2(CHANNEL_B_ID, text)
    assert result is not None
    assert result.symbol == "BTC"
    assert result.side == "LONG"
    assert result.entry == pytest.approx(67400.0)
    assert result.sl == pytest.approx(66900.0)
    assert result.tp == pytest.approx([68200.0])


def test_channel_b_returns_none_for_invalid() -> None:
    result = parse_tier2(CHANNEL_B_ID, "")
    assert result is None


def test_exception_in_parser_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A parser that raises ValueError must not propagate — dispatcher returns None."""

    class _BrokenParser:
        channel_id = -9999
        name = "broken"

        def parse(self, text: str) -> ParsedSignalFields | None:
            raise ValueError("simulated parser failure")

    broken = _BrokenParser()
    PARSERS[-9999] = broken  # type: ignore[assignment]
    try:
        result = parse_tier2(-9999, "LONG BTCUSDT Entry: 67500 SL: 66800 TP1: 68200")
        assert result is None
    finally:
        del PARSERS[-9999]


def test_attribute_error_in_parser_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """AttributeError from a parser is also swallowed."""

    class _AttrBrokenParser:
        channel_id = -9998
        name = "attr_broken"

        def parse(self, text: str) -> ParsedSignalFields | None:
            raise AttributeError("NoneType has no attribute 'group'")

    broken = _AttrBrokenParser()
    PARSERS[-9998] = broken  # type: ignore[assignment]
    try:
        result = parse_tier2(-9998, "some text")
        assert result is None
    finally:
        del PARSERS[-9998]


def test_tier2_shim_re_exports_parse_tier2() -> None:
    """tiers.tier2_regex re-exports parse_tier2 and is callable."""
    result = tier2_re_export(CHANNEL_A_ID, "LONG XAUUSD\nEntry: 2350\nSL: 2342\nTP1: 2362")
    assert result is not None
    assert result.symbol == "XAUUSD"


def test_parsers_dict_contains_only_channel_parser_protocol_instances() -> None:
    for cid, parser in PARSERS.items():
        assert isinstance(cid, int)
        assert hasattr(parser, "channel_id")
        assert hasattr(parser, "name")
        assert callable(getattr(parser, "parse", None))
