"""Provider abstraction for Tier 3 LLM extractor."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, field_validator

PROMPT_TEMPLATE = """You extract trading signals from messages. Return JSON only.

Rules:
- If message is NOT a new entry signal (chat, news, position update, recap), \
return {"is_signal": false}.
- If it IS a new entry signal, extract symbol, side, entry, sl, tp, leverage.
- entry can be a number OR a [low, high] zone.
- tp is always an array (1-5 items).
- confidence: 0.0-1.0, your certainty this is a real signal.

Message:
\"\"\"
{text}
\"\"\""""

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "is_signal": {"type": "boolean"},
        "symbol": {"type": "string"},
        "side": {"type": "string", "enum": ["LONG", "SHORT"]},
        "entry": {
            "oneOf": [
                {"type": "number"},
                {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
            ]
        },
        "sl": {"type": "number"},
        "tp": {"type": "array", "items": {"type": "number"}, "minItems": 1},
        "leverage": {"type": ["integer", "null"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["is_signal"],
}


class ProviderError(Exception):
    """Raised by an LLM provider on unrecoverable HTTP or parse failure."""


class LLMResponse(BaseModel):
    """Validated shape of a provider's JSON response."""

    model_config = ConfigDict(strict=True, frozen=True, extra="ignore")

    is_signal: bool
    symbol: str | None = None
    side: str | None = None
    entry: float | list[float] | None = None
    sl: float | None = None
    tp: list[float] | None = None
    leverage: int | None = None
    confidence: float | None = None

    @field_validator("side")
    @classmethod
    def _side_enum(cls, v: str | None) -> str | None:
        if v is not None and v not in ("LONG", "SHORT"):
            msg = f"invalid side: {v!r}"
            raise ValueError(msg)
        return v


@runtime_checkable
class LLMProvider(Protocol):
    """Async provider that returns a raw dict matching RESPONSE_SCHEMA."""

    name: str

    async def extract(self, text: str) -> dict[str, Any]: ...
