"""Unit tests for the _norm_num number normalisation helper."""

from __future__ import annotations

import pytest

from tg_listener.parsers.numbers import _norm_num


def test_plain_integer() -> None:
    assert _norm_num("67500") == 67500.0


def test_plain_float() -> None:
    assert _norm_num("3.14") == pytest.approx(3.14)


def test_comma_thousands() -> None:
    assert _norm_num("67,500") == 67500.0


def test_comma_thousands_with_decimal() -> None:
    assert _norm_num("1,200.50") == pytest.approx(1200.50)


def test_k_lowercase() -> None:
    assert _norm_num("3.5k") == pytest.approx(3500.0)


def test_k_uppercase() -> None:
    assert _norm_num("3.5K") == pytest.approx(3500.0)


def test_m_lowercase() -> None:
    assert _norm_num("1.2m") == pytest.approx(1_200_000.0)


def test_m_uppercase() -> None:
    assert _norm_num("1.2M") == pytest.approx(1_200_000.0)


def test_leading_whitespace() -> None:
    assert _norm_num("  67500") == 67500.0


def test_trailing_whitespace() -> None:
    assert _norm_num("67500  ") == 67500.0


def test_both_whitespace() -> None:
    assert _norm_num("  67500  ") == 67500.0


def test_integer_k() -> None:
    assert _norm_num("100k") == 100_000.0


def test_integer_m() -> None:
    assert _norm_num("2M") == 2_000_000.0


def test_raises_on_empty_string() -> None:
    with pytest.raises(ValueError):
        _norm_num("")


def test_raises_on_whitespace_only() -> None:
    with pytest.raises(ValueError):
        _norm_num("   ")


def test_raises_on_alpha() -> None:
    with pytest.raises(ValueError):
        _norm_num("abc")


def test_raises_on_ambiguous_dots() -> None:
    with pytest.raises(ValueError):
        _norm_num("1.2.3")


def test_raises_on_suffix_only() -> None:
    with pytest.raises(ValueError):
        _norm_num("k")


def test_zero() -> None:
    assert _norm_num("0") == 0.0


def test_large_comma_number() -> None:
    assert _norm_num("1,000,000") == 1_000_000.0
