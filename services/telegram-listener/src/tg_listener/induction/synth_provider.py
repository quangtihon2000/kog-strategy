"""RegexTableSynthProvider — Protocol + implementations for regex table synthesis.

Separate from LLMProvider (signal extraction) because the prompt shape,
response schema, and call signature differ.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Protocol, runtime_checkable

import httpx

from tg_listener.tiers.llm.base import ProviderError

# ── Protocol ──────────────────────────────────────────────────────────────────


@runtime_checkable
class RegexTableSynthProvider(Protocol):
    """Async provider that takes a prompt and returns a raw dict for RegexTable."""

    name: str

    async def synthesize_table(self, prompt: str) -> dict[str, Any]: ...


# ── Stub (deterministic, for tests) ───────────────────────────────────────────

# Canned response mirroring Channel A's regex_table structure.
_STUB_CANNED_RESPONSE: dict[str, Any] = {
    "side": {
        "pattern": r"(LONG|SHORT)\s+(\w+)",
        "flags": ["IGNORECASE", "UNICODE"],
        "group": 1,
    },
    "side_map": {
        "long": "LONG",
        "short": "SHORT",
    },
    "symbol": None,
    "symbol_from_side_group": 2,
    "entry": {
        "pattern": r"Entry\s*[:=\-]?\s*([\d,.]+[kKmM]?)",
        "flags": ["IGNORECASE", "UNICODE"],
        "group": 1,
    },
    "entry_zone": None,
    "sl": {
        "pattern": r"SL\s*[:=\-]?\s*([\d,.]+[kKmM]?)",
        "flags": ["IGNORECASE", "UNICODE"],
        "group": 1,
    },
    "tp": {
        "pattern": r"TP\d*\s*[:=\-]?\s*([\d,.]+[kKmM]?)",
        "flags": ["IGNORECASE", "UNICODE"],
        "group": 1,
    },
    "tp_split": None,
    "tp_comma_list": None,
    "leverage": None,
    "pre_clean": None,
    "skip_symbols": [],
}


class StubSynthProvider:
    """Queues pre-programmed responses for deterministic testing.

    Mirror of StubLLMProvider in tiers/llm/stub.py.
    """

    name: str = "stub_synth"

    def __init__(
        self,
        responses: list[dict[str, Any] | Exception] | None = None,
    ) -> None:
        # Default: single canned valid response.
        default: list[dict[str, Any] | Exception] = [dict(_STUB_CANNED_RESPONSE)]
        self._queue: deque[dict[str, Any] | Exception] = deque(
            responses if responses is not None else default
        )
        self.call_count: int = 0

    async def synthesize_table(self, prompt: str) -> dict[str, Any]:
        self.call_count += 1
        if not self._queue:
            raise RuntimeError("StubSynthProvider queue exhausted")
        item = self._queue.popleft()
        if isinstance(item, Exception):
            raise item
        return item


# ── Anthropic skeleton ─────────────────────────────────────────────────────────

# Schema passed as a tool so the model is forced to return structured JSON.
_SYNTH_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "A complete RegexTable definition as JSON.",
    "properties": {
        "side": {"type": "object"},
        "side_map": {"type": "object"},
        "symbol": {},
        "symbol_from_side_group": {},
        "entry": {"type": "object"},
        "entry_zone": {},
        "sl": {"type": "object"},
        "tp": {"type": "object"},
        "tp_split": {},
        "tp_comma_list": {},
        "leverage": {},
        "pre_clean": {},
        "skip_symbols": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["side", "side_map", "entry", "sl", "tp", "skip_symbols"],
}


class AnthropicSynthProvider:
    """Calls Anthropic Messages API to synthesize a RegexTable.

    Mirror of AnthropicProvider in tiers/llm/anthropic.py.
    """

    name: str = "anthropic_synth"

    def __init__(
        self,
        api_key: str,
        model: str = "claude-haiku-4-5",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._owned_client = client is None
        self._client = client or httpx.AsyncClient()

    async def synthesize_table(self, prompt: str) -> dict[str, Any]:
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        body = {
            "model": self._model,
            "max_tokens": 2048,
            "tools": [
                {
                    "name": "synthesize_regex_table",
                    "description": "Synthesize a RegexTable from trading signal samples.",
                    "input_schema": _SYNTH_TOOL_SCHEMA,
                }
            ],
            "tool_choice": {"type": "tool", "name": "synthesize_regex_table"},
            "messages": [{"role": "user", "content": prompt}],
        }
        try:
            resp = await self._client.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=body,
                timeout=30.0,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(f"HTTP {exc.response.status_code}") from exc
        except httpx.RequestError as exc:
            raise ProviderError(f"request error: {exc}") from exc

        data = resp.json()
        content: list[dict[str, Any]] = data.get("content", [])
        for block in content:
            if block.get("type") == "tool_use":
                return block["input"]  # type: ignore[no-any-return]

        raise ProviderError("no tool_use block in response")

    async def aclose(self) -> None:
        if self._owned_client:
            await self._client.aclose()
