"""Tier 3 — LLM fallback extractor. Spec section 5.5.

Fires only when Tier 1 passes and Tier 2 returns `None`. Uses Anthropic
Claude Haiku (default) with structured output, sha256-cached in Redis 24h.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging

from pydantic import ValidationError

from tg_listener.models import ParsedSignalFields
from tg_listener.tiers.llm.base import LLMProvider, LLMResponse, ProviderError
from tg_listener.tiers.llm.cache import Tier3Cache

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3
_BACKOFF = (0.5, 1.0)  # seconds between retries 1→2 and 2→3


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _coerce_entry(raw: float | list[float]) -> float | tuple[float, float]:
    if isinstance(raw, list):
        lo, hi = sorted(raw[:2])
        return (lo, hi)
    return float(raw)


def _build_fields(resp: LLMResponse) -> ParsedSignalFields | None:
    """Convert a validated LLMResponse to ParsedSignalFields, or None on incomplete data."""
    if (
        resp.symbol is None
        or resp.side is None
        or resp.entry is None
        or resp.sl is None
        or resp.tp is None
    ):
        return None
    try:
        return ParsedSignalFields(
            symbol=resp.symbol,
            side=resp.side,  # type: ignore[arg-type]
            entry=_coerce_entry(resp.entry),
            sl=float(resp.sl),
            tp=resp.tp,
            leverage=resp.leverage,
            confidence=resp.confidence if resp.confidence is not None else 1.0,
        )
    except (ValidationError, TypeError, ValueError):
        return None


async def extract_tier3(
    text: str,
    provider: LLMProvider,
    cache: Tier3Cache | None = None,
) -> ParsedSignalFields | None:
    """Run Tier 3 LLM extraction with caching and retry.

    Returns ParsedSignalFields when the LLM identifies a valid signal,
    or None when it says not-a-signal, encounters unrecoverable errors, or
    the response fails validation.
    """
    sha = _sha256(text)

    if cache is not None:
        lookup = await cache.get(sha)
        if lookup.hit:
            return lookup.value

    result: ParsedSignalFields | None = None

    for attempt in range(_MAX_ATTEMPTS):
        try:
            raw = await asyncio.wait_for(provider.extract(text), timeout=5.0)
        except TimeoutError:
            logger.warning("tier3 timeout attempt=%d/%d", attempt + 1, _MAX_ATTEMPTS)
        except ProviderError as exc:
            logger.warning(
                "tier3 provider error attempt=%d/%d: %s", attempt + 1, _MAX_ATTEMPTS, exc
            )
        else:
            try:
                resp = LLMResponse.model_validate(raw, strict=False)
            except ValidationError as exc:
                logger.warning("tier3 LLMResponse validation failed: %s", exc)
                break

            if not resp.is_signal:
                result = None
                break

            result = _build_fields(resp)
            if result is None:
                logger.warning("tier3 signal flagged but fields incomplete/invalid")
            break

        # Retry only on timeout/provider error; break immediately on validation issues.
        if attempt < _MAX_ATTEMPTS - 1:
            await asyncio.sleep(_BACKOFF[attempt])
        # After exhausting all attempts, result stays None.

    if cache is not None:
        await cache.set(sha, result)

    return result
