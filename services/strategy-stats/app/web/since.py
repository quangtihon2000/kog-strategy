"""Shared `?since=` parser for dashboard routes.

Accepts `1d`, `7d`, `30d`, `90d`, `all`. Returns epoch seconds the routes hand
to the aggregators. `all` returns 0 so the `signal_ts >= since` predicate
matches everything we have.
"""
from __future__ import annotations

import time

# Order matters for the dropdown rendering in templates.
SINCE_CHOICES: list[tuple[str, str]] = [
    ("1d", "Last 24h"),
    ("7d", "Last 7d"),
    ("30d", "Last 30d"),
    ("90d", "Last 90d"),
    ("all", "All"),
]
DEFAULT_SINCE = "7d"


def since_to_epoch(since: str | None) -> int:
    code = (since or DEFAULT_SINCE).lower()
    if code == "all":
        return 0
    if code.endswith("d") and code[:-1].isdigit():
        days = int(code[:-1])
        return int(time.time()) - days * 86_400
    # Unknown values fall back to default rather than 500-ing the page.
    days = int(DEFAULT_SINCE[:-1])
    return int(time.time()) - days * 86_400


def normalize_since(since: str | None) -> str:
    code = (since or DEFAULT_SINCE).lower()
    valid = {c for c, _ in SINCE_CHOICES}
    return code if code in valid else DEFAULT_SINCE
