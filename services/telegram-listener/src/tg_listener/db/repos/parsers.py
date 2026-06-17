"""ParserRepo — versioning, single-active invariant, propose→activate flow."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tg_listener.db.models import Parser


class ParserRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_active(self, channel_id: int) -> Parser | None:
        """Return the single active parser for a channel, or None."""
        result = await self._session.execute(
            select(Parser).where(
                Parser.channel_id == channel_id,
                Parser.status == "active",
            )
        )
        return result.scalar_one_or_none()

    async def list_versions(self, channel_id: int) -> list[Parser]:
        """Return all parser versions for a channel, ordered by version asc."""
        result = await self._session.execute(
            select(Parser)
            .where(Parser.channel_id == channel_id)
            .order_by(Parser.version)
        )
        return list(result.scalars().all())

    async def propose(
        self,
        channel_id: int,
        regex_table: dict,  # type: ignore[type-arg]
        *,
        source: str = "manual",
        notes: str | None = None,
    ) -> Parser:
        """Create a new parser in 'proposed' status with the next version number."""
        existing = await self.list_versions(channel_id)
        next_version = max((p.version for p in existing), default=0) + 1
        parser = Parser(
            channel_id=channel_id,
            version=next_version,
            status="proposed",
            regex_table=regex_table,
            source=source,
            notes=notes,
        )
        self._session.add(parser)
        await self._session.flush()
        return parser

    async def activate(self, parser_id: int) -> Parser:
        """Retire the current active parser (if any) and activate the given one."""
        result = await self._session.execute(
            select(Parser).where(Parser.id == parser_id)
        )
        parser = result.scalar_one_or_none()
        if parser is None:
            raise ValueError(f"Parser {parser_id} not found")

        # Retire existing active parser for this channel.
        await self._session.execute(
            update(Parser)
            .where(
                Parser.channel_id == parser.channel_id,
                Parser.status == "active",
            )
            .values(status="retired")
        )

        parser.status = "active"
        parser.activated_at = datetime.now(UTC)
        await self._session.flush()
        return parser

    async def reject(self, parser_id: int) -> Parser:
        """Mark a proposed/shadow parser as rejected."""
        result = await self._session.execute(
            select(Parser).where(Parser.id == parser_id)
        )
        parser = result.scalar_one_or_none()
        if parser is None:
            raise ValueError(f"Parser {parser_id} not found")
        parser.status = "rejected"
        await self._session.flush()
        return parser
