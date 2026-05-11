"""Ollama local LLM provider for Tier 3 extractor."""

from __future__ import annotations

import json
from typing import Any

import httpx

from tg_listener.tiers.llm.base import PROMPT_TEMPLATE, ProviderError


class OllamaProvider:
    """Calls a local Ollama instance with JSON-mode output."""

    name: str = "ollama"

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "llama3.1:8b",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._owned_client = client is None
        self._client = client or httpx.AsyncClient()

    async def extract(self, text: str) -> dict[str, Any]:
        body = {
            "model": self._model,
            "stream": False,
            "format": "json",
            "messages": [
                {"role": "user", "content": PROMPT_TEMPLATE.format(text=text)}
            ],
        }
        try:
            resp = await self._client.post(
                f"{self._base_url}/api/chat",
                json=body,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(f"HTTP {exc.response.status_code}") from exc
        except httpx.RequestError as exc:
            raise ProviderError(f"request error: {exc}") from exc

        data = resp.json()
        try:
            content: str = data["message"]["content"]
            return json.loads(content)  # type: ignore[no-any-return]
        except (KeyError, json.JSONDecodeError) as exc:
            raise ProviderError(f"parse error: {exc}") from exc

    async def aclose(self) -> None:
        if self._owned_client:
            await self._client.aclose()
