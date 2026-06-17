"""Anthropic Claude provider for Tier 3 LLM extractor."""

from __future__ import annotations

from typing import Any

import httpx

from tg_listener.tiers.llm.base import PROMPT_TEMPLATE, RESPONSE_SCHEMA, ProviderError


class AnthropicProvider:
    """Calls Anthropic Messages API with forced tool-use for structured output."""

    name: str = "anthropic"

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

    async def extract(self, text: str) -> dict[str, Any]:
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        body = {
            "model": self._model,
            "max_tokens": 1024,
            "tools": [
                {
                    "name": "extract_signal",
                    "description": "Extract a trading signal from the message.",
                    "input_schema": RESPONSE_SCHEMA,
                }
            ],
            "tool_choice": {"type": "tool", "name": "extract_signal"},
            "messages": [
                {"role": "user", "content": PROMPT_TEMPLATE.format(text=text)}
            ],
        }
        try:
            resp = await self._client.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=body,
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
