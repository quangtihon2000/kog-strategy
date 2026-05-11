"""Pydantic v2 models for the regex_table JSONB column.

A RegexTable encodes a complete parsing strategy as data.
RegexEngine (regex_engine.py) interprets it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class RegexSlot(BaseModel):
    """One named regex slot in a RegexTable."""

    pattern: str
    """Raw regex string — compiled with flags specified in `flags`."""

    flags: list[Literal["IGNORECASE", "DOTALL", "MULTILINE", "UNICODE"]] = [
        "IGNORECASE",
        "UNICODE",
    ]

    group: int = 1
    """Which capture group to extract; 0 = whole match."""


class RegexTable(BaseModel):
    """Complete data-driven parser definition stored in parsers.regex_table."""

    model_config = ConfigDict(strict=True, extra="forbid")

    side: RegexSlot
    """Required — matches the side keyword (LONG/SHORT/mua/bán/…)."""

    side_map: dict[str, Literal["LONG", "SHORT"]]
    """Case-insensitive mapping: lowercase keyword → canonical side."""

    symbol: RegexSlot | None = None
    """Optional separate symbol regex. If None, symbol comes from the side match."""

    symbol_from_side_group: int | None = None
    """If set, extract symbol from this capture group of the side regex."""

    entry: RegexSlot
    """Single-value entry pattern."""

    entry_zone: RegexSlot | None = None
    """Optional zone entry — must have exactly 2 capture groups (lo, hi)."""

    sl: RegexSlot
    """Stop-loss pattern."""

    tp: RegexSlot
    """Take-profit pattern — used with finditer; each match yields group `group`."""

    tp_split: str | None = None
    """If set, a regex applied to each TP match to split further tokens."""

    tp_comma_list: str | None = None
    """If set, a regex whose matches are replaced by space before tp_split."""

    leverage: RegexSlot | None = None
    """Optional leverage pattern."""

    pre_clean: str | None = None
    """If set, `re.sub(pre_clean, ' ', text)` is applied before all other parsing."""

    skip_symbols: list[str] = []
    """Uppercase tokens that are NOT valid symbols (used by symbol regex search)."""
