"""ChannelRepo — get, upsert, set_auto_approve."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_listener.db.models import Channel


class ChannelRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, channel_id: int) -> Channel | None:
        result = await self._session.execute(
            select(Channel).where(Channel.id == channel_id)
        )
        return result.scalar_one_or_none()

    async def upsert(self, channel_id: int, name: str) -> Channel:
        """Insert or update the channel name. Returns the persisted Channel."""
        channel = await self.get(channel_id)
        if channel is None:
            channel = Channel(id=channel_id, name=name)
            self._session.add(channel)
        else:
            channel.name = name
            channel.updated_at = datetime.now(UTC)
        await self._session.flush()
        return channel

    async def set_auto_approve(self, channel_id: int, *, value: bool) -> Channel:
        channel = await self.get(channel_id)
        if channel is None:
            raise ValueError(f"Channel {channel_id} not found")
        channel.auto_approve = value
        channel.updated_at = datetime.now(UTC)
        await self._session.flush()
        return channel
