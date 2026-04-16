"""Redis Stream consumer — extensibility hinge for the message backend."""

import logging
from typing import Optional, Tuple

import redis as redis_lib

from config import Settings

log = logging.getLogger(__name__)

# Type alias: (stream_msg_id, field_dict)
Message = Tuple[str, dict]


class RedisConsumer:
    """
    Reads one message at a time from a Redis Stream using consumer groups.

    Consumer groups provide at-least-once delivery: a message is not
    removed from the stream until `ack()` is called.  If the agent crashes
    between `consume_one` and `ack`, the message can be reclaimed via
    XAUTOCLAIM on the next startup (not implemented here, but the stream
    retains the pending entry automatically).

    Swap path — to switch to a simple BRPOP queue, only replace the body
    of `consume_one` and `ack`:
        def consume_one(self, block_ms=5000):
            result = self._r.brpop(self._stream, timeout=block_ms // 1000)
            if result is None:
                return None
            _, raw = result
            # parse raw bytes into a dict and return a synthetic id
            ...
    """

    def __init__(self, settings: Settings) -> None:
        self._r = redis_lib.from_url(settings.redis_url, decode_responses=True)
        self._stream = settings.redis_stream
        self._group = settings.redis_group
        self._consumer = settings.redis_consumer

    # ------------------------------------------------------------------
    def create_group_if_missing(self) -> None:
        """Create the consumer group, silently ignoring if it already exists."""
        try:
            # MKSTREAM creates the stream if it doesn't exist yet
            self._r.xgroup_create(self._stream, self._group, id="0", mkstream=True)
            log.info("Consumer group '%s' created on stream '%s'", self._group, self._stream)
        except redis_lib.exceptions.ResponseError as exc:
            if "BUSYGROUP" in str(exc):
                log.debug("Consumer group '%s' already exists", self._group)
            else:
                raise

    # ------------------------------------------------------------------
    def consume_one(self, block_ms: int = 5000) -> Optional[Message]:
        """
        Block for up to `block_ms` milliseconds waiting for the next message.

        Returns (msg_id, data_dict) or None on timeout.
        """
        results = self._r.xreadgroup(
            groupname=self._group,
            consumername=self._consumer,
            streams={self._stream: ">"},
            count=1,
            block=block_ms,
        )
        if not results:
            return None

        # results: [(stream_name, [(msg_id, {field: value, ...})])]
        _, messages = results[0]
        msg_id, data = messages[0]
        return msg_id, data

    # ------------------------------------------------------------------
    def ack(self, msg_id: str) -> None:
        """Acknowledge a message — removes it from the pending-entry list."""
        self._r.xack(self._stream, self._group, msg_id)
        log.debug("ACK %s", msg_id)
