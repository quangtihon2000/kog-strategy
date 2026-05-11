"""Generic XREADGROUP consumer loop.

One instance per (stream, group). Reads in batches, dispatches each message to a
handler inside an AsyncSession scope, ACKs only on success — failures stay in
the pending entries list for forensics.

First-time XGROUP CREATE uses `id=0` so we backfill historical messages still
retained in Redis (no `MKSTREAM` is needed since EA agents have already created
the streams).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import redis.asyncio as redis
from redis.exceptions import RedisError, ResponseError

from app.db import session_scope
from app.settings import get_settings

Handler = Callable[[Any, dict[str, str]], Awaitable[None]]

log = logging.getLogger(__name__)


class StreamConsumer:
    def __init__(
        self,
        client: redis.Redis,
        stream: str,
        group: str,
        handler: Handler,
        *,
        consumer_name: str | None = None,
        count: int | None = None,
        block_ms: int | None = None,
    ) -> None:
        s = get_settings()
        self.client = client
        self.stream = stream
        self.group = group
        self.handler = handler
        self.consumer_name = consumer_name or s.ingest_consumer_name
        self.count = count or s.ingest_batch_count
        self.block_ms = block_ms or s.ingest_block_ms

    async def _ensure_group(self) -> None:
        try:
            # id=0 so first-time worker backfills historical messages
            await self.client.xgroup_create(self.stream, self.group, id="0", mkstream=True)
            log.info("created consumer group %s on %s (backfilling from id=0)", self.group, self.stream)
        except ResponseError as exc:
            if "BUSYGROUP" in str(exc):
                log.info("consumer group %s on %s already exists", self.group, self.stream)
                return
            raise

    async def loop(self) -> None:
        await self._ensure_group()
        log.info(
            "StreamConsumer %s/%s ready (consumer=%s count=%d block=%dms)",
            self.stream,
            self.group,
            self.consumer_name,
            self.count,
            self.block_ms,
        )

        while True:
            try:
                resp = await self.client.xreadgroup(
                    groupname=self.group,
                    consumername=self.consumer_name,
                    streams={self.stream: ">"},
                    count=self.count,
                    block=self.block_ms,
                )
            except RedisError as exc:
                log.exception("%s xreadgroup error: %s; sleeping 2s", self.stream, exc)
                await asyncio.sleep(2)
                continue

            if not resp:
                continue

            for _stream_name, messages in resp:
                for msg_id, fields in messages:
                    msg_id_s = _to_str(msg_id)
                    fields_s = {_to_str(k): _to_str(v) for k, v in fields.items()}
                    try:
                        async with session_scope() as session:
                            await self.handler(session, fields_s)
                    except Exception:
                        log.exception(
                            "%s handler error msg_id=%s fields=%s (left in PEL)",
                            self.stream,
                            msg_id_s,
                            fields_s,
                        )
                        continue
                    try:
                        await self.client.xack(self.stream, self.group, msg_id_s)
                    except RedisError:
                        log.exception("%s xack error msg_id=%s", self.stream, msg_id_s)


def _to_str(v: Any) -> str:
    if isinstance(v, bytes):
        return v.decode("utf-8", errors="replace")
    return str(v)
