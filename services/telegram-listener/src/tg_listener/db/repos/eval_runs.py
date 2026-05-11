"""EvalRunRepo — record parser evaluation runs."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_listener.db.models import ParserEvalRun


class EvalRunRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        parser_id: int,
        samples_total: int,
        samples_matched: int,
        disagreements: list[dict],  # type: ignore[type-arg]
    ) -> ParserEvalRun:
        """Record a new eval run result."""
        match_rate = samples_matched / samples_total if samples_total > 0 else 0.0
        run = ParserEvalRun(
            parser_id=parser_id,
            samples_total=samples_total,
            samples_matched=samples_matched,
            match_rate=match_rate,
            disagreements=disagreements,
        )
        self._session.add(run)
        await self._session.flush()
        return run

    async def latest_for_parser(self, parser_id: int) -> ParserEvalRun | None:
        """Return the most recent eval run for a given parser, or None.

        Sắp xếp theo ran_at DESC, id DESC để đảm bảo tính ổn định khi
        nhiều run xảy ra trong cùng một giây.

        Args:
            parser_id: ID của parser cần tra cứu.

        Returns:
            ParserEvalRun mới nhất, hoặc None nếu chưa có run nào.
        """
        result = await self._session.execute(
            select(ParserEvalRun)
            .where(ParserEvalRun.parser_id == parser_id)
            .order_by(ParserEvalRun.ran_at.desc(), ParserEvalRun.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()
