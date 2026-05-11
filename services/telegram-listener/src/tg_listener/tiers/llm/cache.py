"""Redis-backed cache for Tier 3 LLM results."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import redis.asyncio as aioredis

from tg_listener.models import ParsedSignalFields


@dataclass(frozen=True)
class CacheLookup:
    hit: bool
    value: ParsedSignalFields | None = None


class Tier3Cache:
    """sha256(text) → ParsedSignalFields | None cached in Redis."""

    def __init__(
        self,
        redis: aioredis.Redis,
        ttl_seconds: int = 86400,
        namespace: str = "tier3:llm",
    ) -> None:
        self._redis = redis
        self._ttl = ttl_seconds
        self._ns = namespace

    def _key(self, sha: str) -> str:
        return f"{self._ns}:{sha}"

    async def get(self, sha: str) -> CacheLookup:
        raw = await self._redis.get(self._key(sha))
        if raw is None:
            return CacheLookup(hit=False)
        payload: dict[str, Any] = json.loads(raw)
        if payload["value"] is None:
            return CacheLookup(hit=True, value=None)
        # model_dump() serialises tuple entry as list; model_validate handles coercion.
        return CacheLookup(
            hit=True, value=ParsedSignalFields.model_validate(payload["value"], strict=False)
        )

    async def set(self, sha: str, value: ParsedSignalFields | None) -> None:
        payload: dict[str, Any] = {
            "value": value.model_dump() if value is not None else None
        }
        await self._redis.setex(self._key(sha), self._ttl, json.dumps(payload))
