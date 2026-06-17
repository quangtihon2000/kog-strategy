"""Tier 2 — per-channel regex parser dispatcher. Spec section 5.4.

Looks up the registered parser by `channel_id` and returns a
`ParsedSignalFields` or `None`. Errors must be swallowed and logged so a bad
parser cannot crash the pipeline.
"""

from __future__ import annotations

from tg_listener.parsers.dispatcher import parse_tier2 as parse_tier2
