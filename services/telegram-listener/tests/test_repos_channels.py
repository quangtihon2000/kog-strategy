"""Tests for ChannelRepo."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tg_listener.db.repos.channels import ChannelRepo


@pytest.mark.asyncio
async def test_upsert_creates_channel(db_session: AsyncSession) -> None:
    repo = ChannelRepo(db_session)
    ch = await repo.upsert(-1111111111, "My Channel")
    assert ch.id == -1111111111
    assert ch.name == "My Channel"


@pytest.mark.asyncio
async def test_upsert_updates_name(db_session: AsyncSession) -> None:
    repo = ChannelRepo(db_session)
    await repo.upsert(-1111111112, "Old Name")
    ch = await repo.upsert(-1111111112, "New Name")
    assert ch.name == "New Name"


@pytest.mark.asyncio
async def test_get_returns_none_for_missing(db_session: AsyncSession) -> None:
    repo = ChannelRepo(db_session)
    result = await repo.get(-9999000001)
    assert result is None


@pytest.mark.asyncio
async def test_get_returns_channel(db_session: AsyncSession) -> None:
    repo = ChannelRepo(db_session)
    await repo.upsert(-1111111113, "Found")
    ch = await repo.get(-1111111113)
    assert ch is not None
    assert ch.name == "Found"


@pytest.mark.asyncio
async def test_set_auto_approve(db_session: AsyncSession) -> None:
    repo = ChannelRepo(db_session)
    await repo.upsert(-1111111114, "Ch")
    ch = await repo.set_auto_approve(-1111111114, value=True)
    assert ch.auto_approve is True
    ch2 = await repo.set_auto_approve(-1111111114, value=False)
    assert ch2.auto_approve is False


@pytest.mark.asyncio
async def test_set_auto_approve_missing_raises(db_session: AsyncSession) -> None:
    repo = ChannelRepo(db_session)
    with pytest.raises(ValueError, match="not found"):
        await repo.set_auto_approve(-9999000002, value=True)
