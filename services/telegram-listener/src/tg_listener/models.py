"""Pydantic v2 data models for the Telegram signal listener.

See spec section 6 (Data Models) for the authoritative definitions.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ParsedBy = Literal[
    "tier0_metadata",
    "tier1_heuristic",
    "tier2_regex",
    "tier3_llm",
    "tier4_validator",
]
Side = Literal["LONG", "SHORT"]
EntryValue = float | tuple[float, float]


class ParsedSignalFields(BaseModel):
    """Output of Tier 2 / Tier 3, input of Tier 4.

    Spec section 6.
    """

    model_config = ConfigDict(strict=True, frozen=True)

    symbol: str
    side: Side
    entry: EntryValue
    sl: float
    tp: list[float] = Field(min_length=1, max_length=5)
    leverage: int | None = None
    confidence: float = 1.0


class Signal(BaseModel):
    """Validated signal ready to be pushed onto `signals:raw`.

    Spec section 6.
    """

    model_config = ConfigDict(strict=True, frozen=True)

    signal_id: str
    channel_id: int
    channel_name: str
    message_id: int
    received_at: datetime

    symbol: str
    side: Side
    entry: EntryValue
    sl: float
    tp: list[float] = Field(min_length=1, max_length=5)
    leverage: int | None = None

    raw_text: str
    parsed_by: ParsedBy
    confidence: float


class ValidationResult(BaseModel):
    """Result of Tier 4 validation. Spec section 5.6."""

    model_config = ConfigDict(strict=True, frozen=True)

    ok: bool
    reason: str
