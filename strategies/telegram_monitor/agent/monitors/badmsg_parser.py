"""Extract structured info from agent "Bad message" log lines.

The strategy agents (zone_signal, conde_auto_entry, gvfx_signal) all log
validation failures with the same general shape:

    Bad message <msg_id> — discarding: <exc> | raw=<dict_repr>

The separator is em-dash for zone/gvfx and `--` for conde. `<dict_repr>` is
Python's `%r` of a dict — single-quoted strings, not valid JSON, but parseable
with `ast.literal_eval` (which accepts only literals, no code execution).
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

# Anchored on the logger record body. The leading "YYYY-mm-dd HH:MM:SS LEVEL"
# prefix sits to the left of this match, so do not anchor with ^.
# MULTILINE so `$` matches end-of-line (each Bad message log entry is one line);
# no DOTALL — values are always single-line in practice (Redis fields are
# strings) and the line-bounded match prevents a greedy `\{.*\}` from spanning
# multiple Bad message entries when several stack up between scanner ticks.
#
# Separator tolerance: zone/gvfx emit em-dash `—` (U+2014) and conde emits
# `--`. On Windows, NSSM-captured stderr is often cp1252-encoded, so the
# em-dash byte 0x97 isn't valid UTF-8 and our reader replaces it with `�`
# (rendered as `❓`). `[^\w\s]+` matches `—`, `--`, and `�` alike.
_BAD_MSG_RE = re.compile(
    r"Bad message\s+(?P<msg_id>\S+)\s+[^\w\s]+\s+discarding:\s+"
    r"(?P<exc>.+?)\s+\|\s+raw=(?P<raw>\{.*\})\s*$",
    re.MULTILINE,
)


@dataclass(frozen=True)
class BadMessage:
    msg_id: str       # original Redis stream id, e.g. "1778240401849-0"
    exc: str          # exception repr, e.g. "'redbox_upper'" for KeyError
    payload: dict     # parsed dict from raw=%r


def parse_bad_message(text: str) -> BadMessage | None:
    """Find the LAST `Bad message` line in `text` and return its parts.

    Returns None when no match, or when the raw dict can't be parsed as a
    Python literal. Multiple matches in the same chunk are common during
    agent restart spam — the most recent one is the actionable error.
    """
    last = None
    for m in _BAD_MSG_RE.finditer(text):
        last = m
    if last is None:
        return None
    try:
        payload = ast.literal_eval(last.group("raw"))
    except (ValueError, SyntaxError):
        return None
    if not isinstance(payload, dict):
        return None
    return BadMessage(
        msg_id=last.group("msg_id"),
        exc=last.group("exc").strip(),
        payload=payload,
    )
