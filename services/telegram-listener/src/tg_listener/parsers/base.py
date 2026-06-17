"""ChannelParser protocol + shared number normalization. Spec section 5.4.

Each parser implementation (per-channel regex or YAML-driven generic parser)
exposes a `parse(text) -> ParsedSignalFields | None` method. Number parsing
helpers (e.g. `_norm_num`) live here so tier3 / future parsers can reuse them.
"""

from __future__ import annotations

from typing import Protocol

from tg_listener.models import ParsedSignalFields
from tg_listener.parsers.numbers import _norm_num as _norm_num  # re-export


class ChannelParser(Protocol):
    """Interface every per-channel parser must satisfy."""

    channel_id: int
    name: str

    def parse(self, text: str) -> ParsedSignalFields | None: ...
