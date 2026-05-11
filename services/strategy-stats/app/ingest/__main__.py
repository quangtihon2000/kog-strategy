"""Ingest worker entry point.

Spawns 6 StreamConsumer tasks (one per (stream, group)) and runs them
concurrently under one asyncio loop. Consumer-group names are namespaced
`stats_*` so we don't compete with the EA-agent writer groups.
"""
from __future__ import annotations

import asyncio
import logging
import signal

import redis.asyncio as redis

from app.ingest.consumer import StreamConsumer
from app.ingest.streams import (
    conde_outcomes,
    conde_signals,
    gvfx_outcomes,
    gvfx_signals,
    zone_outcomes,
    zone_signals,
)
from app.settings import get_settings

log = logging.getLogger(__name__)


# (base_stream_name, consumer_group, handler). The actual stream name is
# prefixed at runtime with settings.redis_stream_prefix (empty in prod;
# "dev_" / "test_" for staging namespaces).
STREAMS = [
    ("conde_signals", "stats_conde_sig", conde_signals.handle),
    ("conde_outcomes", "stats_conde_out", conde_outcomes.handle),
    ("gvfx_signals", "stats_gvfx_sig", gvfx_signals.handle),
    ("gvfx_outcomes", "stats_gvfx_out", gvfx_outcomes.handle),
    ("zone_signals", "stats_zone_sig", zone_signals.handle),
    ("zone_outcomes", "stats_zone_out", zone_outcomes.handle),
]


async def main() -> None:
    settings = get_settings()
    client = redis.from_url(settings.upstream_redis_url, decode_responses=False)

    prefix = settings.redis_stream_prefix
    consumers = [
        StreamConsumer(client, f"{prefix}{base}", group, handler)
        for base, group, handler in STREAMS
    ]
    log.info("starting %d stream consumers against %s", len(consumers), settings.upstream_redis_url)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    tasks = [asyncio.create_task(c.loop(), name=f"consumer:{c.stream}") for c in consumers]
    stop_task = asyncio.create_task(stop_event.wait(), name="stop-wait")

    try:
        done, pending = await asyncio.wait(
            [*tasks, stop_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in done:
            if t is stop_task:
                log.info("shutdown signal received; cancelling consumers")
            else:
                exc = t.exception()
                if exc is not None:
                    log.error("consumer task %s exited: %s", t.get_name(), exc)
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await client.aclose()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
