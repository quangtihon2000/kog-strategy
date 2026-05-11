"""Stub LLM provider for tests."""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Any


class TimeoutMarker:
    """Sentinel: causes the stub to sleep so asyncio.wait_for fires."""


class StubLLMProvider:
    """Queues pre-programmed responses for deterministic testing."""

    name: str = "stub"

    def __init__(self, responses: list[dict[str, Any] | Exception | TimeoutMarker]) -> None:
        self._queue: deque[dict[str, Any] | Exception | TimeoutMarker] = deque(responses)
        self.call_count: int = 0

    async def extract(self, text: str) -> dict[str, Any]:
        self.call_count += 1
        if not self._queue:
            raise RuntimeError("StubLLMProvider queue exhausted")
        item = self._queue.popleft()
        if isinstance(item, TimeoutMarker):
            await asyncio.sleep(9999)
            raise RuntimeError("unreachable")  # pragma: no cover
        if isinstance(item, Exception):
            raise item
        return item
