"""Tier 1 — heuristic pre-filter. Spec section 5.3.

Cheap keyword + length check that drops 80%+ of regular chat before any
regex / LLM work happens.

Keyword lists are tuned for the bilingual (Vietnamese + English) signal
channels listed in spec §1. Tweak via audit loop (spec §10), not by
guessing — `signals:rejected_sample` is the source of truth.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict

# Greetings / digest-style headers — only checked against the message head.
ANTI_HEAD: tuple[str, ...] = (
    "gm",
    "good morning",
    "chào sáng",
    "kết quả",
    "recap",
    "tuần qua",
    "hôm qua",
    "phân tích",
    "nhận định",
    "view tuần",
    "vip",
    "premium",
    "subscribe",
    "inbox",
    "liên hệ",
)

# Position-update phrases — anywhere in the message means "not a new entry".
ANTI_ANYWHERE: tuple[str, ...] = (
    "tp1 hit",
    "tp2 hit",
    "tp3 hit",
    "tp4 hit",
    "tp5 hit",
    "sl hit",
    "closed",
    "đóng lệnh",
    "chốt lệnh",
    "move sl",
    "moved to",
    "breakeven",
    " be ",
    "trailing",
    "cancel order",
    "hủy lệnh",
)

# Side / direction tokens. Matched as whole words to avoid hits inside
# unrelated words (e.g. "buy" inside "abuying" — unlikely, but cheap).
POSITIVE_SIDE: tuple[str, ...] = (
    "long",
    "short",
    "buy",
    "sell",
    "mua",
    "bán",
)

# Price-anchor keywords — if a message lacks all of these it almost
# certainly isn't an entry signal.
POSITIVE_PRICE_KW: tuple[str, ...] = (
    "entry",
    "sl",
    "tp",
    "stop",
    "target",
    "cắt lỗ",
    "mục tiêu",
)

_MIN_LEN = 20
_MAX_LEN = 1500
_HEAD_WINDOW = 40

_NUMBER_RE = re.compile(r"\d{2,}")
# Word-boundary side regex built once at import; cheaper than `in` per call.
_SIDE_RE = re.compile(
    r"(?<![\w])(?:" + "|".join(re.escape(w) for w in POSITIVE_SIDE) + r")(?![\w])",
    re.IGNORECASE,
)

Tier1Action = Literal["pass", "reject"]


class Tier1Decision(BaseModel):
    """Result of `evaluate`. `reason` labels Prometheus rejects (spec §9)."""

    model_config = ConfigDict(strict=True, frozen=True)

    action: Tier1Action
    reason: str


def is_likely_signal(text: str) -> bool:
    """Return True iff `text` looks like a new entry signal (spec 5.3)."""
    return evaluate(text).action == "pass"


def evaluate(text: str) -> Tier1Decision:
    """Same gate as `is_likely_signal` but returns a labelled decision.

    Order matches spec 5.3: anti-head → anti-anywhere → length → positive
    triple (side keyword + price keyword + 2+ digit number).
    """
    if not text:
        return Tier1Decision(action="reject", reason="empty_text")

    lowered = text.lower()
    head = lowered[:_HEAD_WINDOW]

    if any(kw in head for kw in ANTI_HEAD):
        return Tier1Decision(action="reject", reason="anti_head_keyword")

    if any(kw in lowered for kw in ANTI_ANYWHERE):
        return Tier1Decision(action="reject", reason="anti_anywhere_keyword")

    n = len(text)
    if n <= _MIN_LEN:
        return Tier1Decision(action="reject", reason="too_short")
    if n >= _MAX_LEN:
        return Tier1Decision(action="reject", reason="too_long")

    if not _SIDE_RE.search(lowered):
        return Tier1Decision(action="reject", reason="no_side_keyword")

    if not any(kw in lowered for kw in POSITIVE_PRICE_KW):
        return Tier1Decision(action="reject", reason="no_price_keyword")

    if not _NUMBER_RE.search(text):
        return Tier1Decision(action="reject", reason="no_number")

    return Tier1Decision(action="pass", reason="ok")
