"""Per-strategy signal JSON formatters.

The bot's `/signals` shows the *content* of the newest signal file. Each
strategy has a different schema (zone_signal: redbox + targets; conde:
direction + entry/sl/tps), so we dispatch by service name. Unknown
schemas fall back to compact JSON dump so a new strategy is still
readable before its formatter lands.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Callable


def _ts_to_str(ts: int | float | None) -> str:
    if not ts:
        return "-"
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except (ValueError, OSError, OverflowError):
        return str(ts)


def _fmt_price_list(values) -> str:
    if not values:
        return "-"
    return ", ".join(f"{float(v):g}" for v in values)


def format_zone_signal(d: dict) -> str:
    sym = d.get("symbol", "?")
    upper = d.get("redbox_upper")
    lower = d.get("redbox_lower")
    return (
        f"symbol     : {sym}\n"
        f"timestamp  : {_ts_to_str(d.get('timestamp'))}\n"
        f"redbox     : {lower} — {upper}\n"
        f"targets ↑  : {_fmt_price_list(d.get('targets_above'))}\n"
        f"targets ↓  : {_fmt_price_list(d.get('targets_below'))}"
    )


def format_conde_signal(d: dict) -> str:
    direction = str(d.get("direction", "?")).upper()
    arrow = "▲" if direction == "BUY" else ("▼" if direction == "SELL" else "·")
    return (
        f"symbol     : {d.get('symbol', '?')}\n"
        f"timestamp  : {_ts_to_str(d.get('timestamp'))}\n"
        f"direction  : {arrow} {direction}\n"
        f"entry      : {d.get('entry_price', '-')}\n"
        f"sl         : {d.get('sl', '-')}\n"
        f"tps        : {_fmt_price_list(d.get('tps'))}"
    )


def format_gvfx_signal(d: dict) -> str:
    direction = str(d.get("direction", "?")).upper()
    arrow = "▲" if direction == "BUY" else ("▼" if direction == "SELL" else "·")
    return (
        f"symbol     : {d.get('symbol', '?')}\n"
        f"timestamp  : {_ts_to_str(d.get('timestamp'))}\n"
        f"direction  : {arrow} {direction}\n"
        f"target     : {d.get('target', '-')}\n"
        f"step       : {d.get('step', '-')} pts\n"
        f"tp         : {d.get('tp', '-')} pts"
    )


def format_generic(d: dict) -> str:
    return json.dumps(d, indent=2, ensure_ascii=False, sort_keys=True)


_FORMATTERS: dict[str, Callable[[dict], str]] = {
    "zone_signal": format_zone_signal,
    "conde_auto_entry": format_conde_signal,
    "gvfx_signal": format_gvfx_signal,
}


def format_signal(service_name: str, data: dict) -> str:
    return _FORMATTERS.get(service_name, format_generic)(data)
