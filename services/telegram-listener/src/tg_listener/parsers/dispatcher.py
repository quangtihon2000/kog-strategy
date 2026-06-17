"""Channel-id → parser dispatch table. Spec section 5.4.

Uses an in-memory cache (`parsers.cache`) keyed by channel_id.  Cache is
populated from DB at startup and refreshed every 60 s; tests bootstrap from
`db/seed_data.py` via `cache.prime_from_seed()`.

`PARSERS` is kept for backward compatibility with existing tests and tooling;
it is populated with `_CacheBackedParser` adapters that proxy through the
cache at call time.  Direct `parse_tier2` calls bypass the dict and query
the cache directly for efficiency.
"""

from __future__ import annotations

import logging

from tg_listener.db.seed_data import CHANNEL_A_ID, CHANNEL_B_ID
from tg_listener.models import ParsedSignalFields
from tg_listener.parsers import cache, regex_engine
from tg_listener.parsers.base import ChannelParser

log = logging.getLogger(__name__)


class _CacheBackedParser:
    """Thin adapter that satisfies `ChannelParser` via cache + regex_engine.

    Keeps `PARSERS` dict usable for legacy tests that inspect protocol attributes.
    """

    def __init__(self, channel_id: int, name: str) -> None:
        self.channel_id: int = channel_id
        self.name: str = name

    def parse(self, text: str) -> ParsedSignalFields | None:
        """Delegate to cache-backed regex_engine."""
        table = cache.get_table(self.channel_id)
        if table is None:
            # Lazy bootstrap from seed for dev / tests where DB isn't wired.
            try:
                cache.prime_from_seed()
                table = cache.get_table(self.channel_id)
            except Exception as e:
                log.debug(
                    "dispatcher_seed_prime_failed", extra={"error": str(e)}
                )
            if table is None:
                return None
        try:
            return regex_engine.parse(table, text)
        except (ValueError, AttributeError, IndexError) as e:
            log.debug(
                "tier2_parse_error",
                extra={"chan": self.channel_id, "error": str(e)},
            )
            return None


_channel_a = _CacheBackedParser(CHANNEL_A_ID, "Channel A Pro")
_channel_b = _CacheBackedParser(CHANNEL_B_ID, "Channel B VN")

# Kept for backward compatibility — existing tests import and inspect PARSERS.
PARSERS: dict[int, ChannelParser] = {
    _channel_a.channel_id: _channel_a,
    _channel_b.channel_id: _channel_b,
}


def parse_tier2(channel_id: int, text: str) -> ParsedSignalFields | None:
    """Dispatch `text` to the cache-backed parser for `channel_id`.

    Returns `None` if no table is registered for `channel_id` or if
    regex_engine raises (ValueError, AttributeError, IndexError) — errors are
    logged at DEBUG and swallowed so a buggy table cannot crash the pipeline.
    """
    table = cache.get_table(channel_id)
    if table is None:
        # Lazy bootstrap from seed for dev / tests where DB isn't wired.
        try:
            cache.prime_from_seed()
            table = cache.get_table(channel_id)
        except Exception as e:
            log.debug(
                "dispatcher_seed_prime_failed", extra={"error": str(e)}
            )
        if table is None:
            return None
    try:
        return regex_engine.parse(table, text)
    except (ValueError, AttributeError, IndexError) as e:
        log.debug("tier2_parse_error", extra={"chan": channel_id, "error": str(e)})
        return None
