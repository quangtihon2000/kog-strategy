"""Tests for induction.minhash — fingerprint determinism + Jaccard estimates."""

from __future__ import annotations

from tg_listener.induction.minhash import M, fingerprint, jaccard


def test_fingerprint_is_deterministic() -> None:
    text = "BTC LONG entry 67400 sl 66900 tp 68200"
    assert fingerprint(text) == fingerprint(text)


def test_fingerprint_length_is_M() -> None:
    assert len(fingerprint("hello world")) == M


def test_identical_texts_have_jaccard_1() -> None:
    fp = fingerprint("LONG XAUUSD entry 2350 sl 2342 tp 2362")
    assert jaccard(fp, fp) == 1.0


def test_distinct_texts_have_low_jaccard() -> None:
    fp_a = fingerprint("LONG XAUUSD entry 2350 sl 2342 tp 2362")
    fp_b = fingerprint("Mua BTCUSDT vùng 67400 cắt lỗ 66900 mục tiêu 68200")
    assert jaccard(fp_a, fp_b) < 0.3


def test_similar_texts_have_high_jaccard() -> None:
    fp_a = fingerprint("LONG XAUUSD entry 2350 sl 2342 tp 2362")
    fp_b = fingerprint("LONG XAUUSD entry 2351 sl 2342 tp 2362")  # one digit changed
    assert jaccard(fp_a, fp_b) >= 0.7


def test_empty_text_handled() -> None:
    fp = fingerprint("")
    assert len(fp) == M


def test_short_text_handled() -> None:
    """Text shorter than K=5 still produces a fingerprint."""
    fp = fingerprint("BTC")
    assert len(fp) == M
