"""SampleCollector — diverse-corpus collector for Tier 3 LLM successes.

Orchestrator calls `collect(channel_id, text, parsed, parsed_by)` after
every successful Tier 3 extraction. The collector:
  1. Drops samples below `confidence_threshold` (default 0.85).
  2. Computes MinHash fingerprint of the text and compares against the most
     recent `recent_n` samples for the channel; drops if max Jaccard >=
     `minhash_threshold` (default 0.85).
  3. Persists survivors via SampleRepo (sha256 text-dedup is enforced by
     the unique index on (channel_id, text_hash)).

Returns True if the sample was persisted, False otherwise.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tg_listener.db.repos.samples import SampleRepo
from tg_listener.induction.minhash import fingerprint, jaccard
from tg_listener.models import ParsedSignalFields

log = logging.getLogger(__name__)


@dataclass(slots=True)
class CollectorConfig:
    confidence_threshold: float = 0.85
    minhash_threshold: float = 0.85
    recent_n: int = 100


class SampleCollector:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        config: CollectorConfig | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._config = config or CollectorConfig()

    async def collect(
        self,
        channel_id: int,
        text: str,
        parsed: ParsedSignalFields,
        parsed_by: str = "tier3_llm",
    ) -> bool:
        """Collect a sample if it passes confidence and near-duplicate filters.

        Args:
            channel_id: Telegram channel ID (FK in DB).
            text: Raw message text to store.
            parsed: Extracted signal fields from Tier 3 LLM.
            parsed_by: Label identifying the parser that produced this result.

        Returns:
            True if the sample was persisted, False if dropped.
        """
        cfg = self._config
        if parsed.confidence < cfg.confidence_threshold:
            log.debug(
                "sample_collector_low_confidence",
                extra={"chan": channel_id, "confidence": parsed.confidence},
            )
            return False

        async with self._session_factory() as session:
            samples_repo = SampleRepo(session)
            recent = await samples_repo.list_for_channel(
                channel_id, limit=cfg.recent_n, offset=0
            )
            if recent:
                fp_new = fingerprint(text)
                max_sim = max(jaccard(fp_new, fingerprint(s.text)) for s in recent)
                if max_sim >= cfg.minhash_threshold:
                    log.debug(
                        "sample_collector_near_duplicate",
                        extra={"chan": channel_id, "max_jaccard": max_sim},
                    )
                    return False

            # Use mode="json" so tuple[float, float] entry serializes to a list,
            # which round-trips cleanly through JSONB.
            row = await samples_repo.insert_if_new(
                channel_id=channel_id,
                text=text,
                parsed_by=parsed_by,
                parsed_signal=parsed.model_dump(mode="json"),
                confidence=parsed.confidence,
            )
            await session.commit()
            return row is not None
