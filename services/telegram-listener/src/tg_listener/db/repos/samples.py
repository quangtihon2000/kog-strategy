"""SampleRepo — insert_if_new, list_for_channel, count."""

from __future__ import annotations

import hashlib

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tg_listener.db.models import ParserSample


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class SampleRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert_if_new(
        self,
        channel_id: int,
        text: str,
        parsed_by: str,
        parsed_signal: dict,  # type: ignore[type-arg]
        confidence: float,
    ) -> ParserSample | None:
        """Insert sample if the (channel_id, text_hash) pair is new.

        Returns the new row or None if it already existed.
        """
        text_hash = _sha256(text)
        stmt = (
            pg_insert(ParserSample)
            .values(
                channel_id=channel_id,
                text_hash=text_hash,
                text=text,
                parsed_by=parsed_by,
                parsed_signal=parsed_signal,
                confidence=confidence,
            )
            .on_conflict_do_nothing(
                index_elements=["channel_id", "text_hash"],
            )
            .returning(ParserSample.id)
        )
        result = await self._session.execute(stmt)
        row = result.fetchone()
        if row is None:
            return None
        return await self._session.get(ParserSample, row[0])

    async def list_for_channel(
        self,
        channel_id: int,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ParserSample]:
        result = await self._session.execute(
            select(ParserSample)
            .where(ParserSample.channel_id == channel_id)
            .order_by(ParserSample.collected_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def count(self, channel_id: int) -> int:
        result = await self._session.execute(
            select(func.count()).where(ParserSample.channel_id == channel_id)
        )
        return result.scalar_one()

    async def count_by_parsed_by(self, channel_id: int) -> dict[str, int]:
        """Aggregate sample counts grouped by parsed_by tier label.

        Args:
            channel_id: Telegram channel ID.

        Returns:
            Dict mapping parsed_by → count, e.g. {"tier3_llm": 120, "heuristic": 4}.
            Returns {} for channels with no samples.
        """
        result = await self._session.execute(
            select(ParserSample.parsed_by, func.count().label("cnt"))
            .where(ParserSample.channel_id == channel_id)
            .group_by(ParserSample.parsed_by)
        )
        return {row.parsed_by: row.cnt for row in result.all()}
