"""Per-strategy specs for republishing fixed bad messages.

Each entry maps a `fleet.yaml` service name to the Redis stream the agent
consumes from, the JSON keys that must be present in a fix, and a
`flatten()` that converts the operator-edited JSON dict back to the flat
str-only field map Redis Streams use.

Field shapes mirror the agent `models.py` files. We deliberately don't
import them — the bot runs in a different venv, and the agent re-validates
on consumption anyway, so a republish that's still bad just produces another
Bad message alert with another Edit button.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class StrategySpec:
    service_name: str
    stream: str
    required_fields: tuple[str, ...]
    optional_fields: tuple[str, ...]
    flatten: Callable[[dict], dict[str, str]]


def _csv(v: Any) -> str:
    if isinstance(v, list):
        return ",".join(str(x) for x in v)
    return str(v)


def _flatten_zone(d: dict[str, Any]) -> dict[str, str]:
    # zone_signal re-stamps timestamp at write time; we still send a fresh one.
    return {
        "timestamp": str(int(time.time())),
        "symbol": str(d["symbol"]),
        "redbox_upper": str(d["redbox_upper"]),
        "redbox_lower": str(d["redbox_lower"]),
        "targets_above": _csv(d["targets_above"]),
        "targets_below": _csv(d["targets_below"]),
    }


def _flatten_conde(d: dict[str, Any]) -> dict[str, str]:
    # PRESERVE producer timestamp — embedded in EA position comments for dedup.
    return {
        "timestamp": str(int(d["timestamp"])),
        "symbol": str(d["symbol"]),
        "direction": str(d["direction"]).upper(),
        "entry_price": str(d["entry_price"]),
        "sl": str(d["sl"]),
        "tps": _csv(d["tps"]),
        "channel_id": str(int(d["channel_id"])),
        "channel_name": str(d.get("channel_name", "")),
    }


def _flatten_gvfx(d: dict[str, Any]) -> dict[str, str]:
    # PRESERVE producer timestamp — embedded in EA position comments for dedup.
    out = {
        "timestamp": str(int(d["timestamp"])),
        "symbol": str(d["symbol"]),
        "target": str(d["target"]),
        "direction": str(d["direction"]).upper(),
        "step": str(int(d["step"])),
        "tp": str(int(d["tp"])),
    }
    if "low" in d:
        out["low"] = str(d["low"])
    if "high" in d:
        out["high"] = str(d["high"])
    if "use_atr" in d:
        out["use_atr"] = "true" if bool(d["use_atr"]) else "false"
    return out


REGISTRY: dict[str, StrategySpec] = {
    "zone_signal": StrategySpec(
        service_name="zone_signal",
        stream="zone_signals",
        required_fields=(
            "symbol", "redbox_upper", "redbox_lower",
            "targets_above", "targets_below",
        ),
        optional_fields=("timestamp",),
        flatten=_flatten_zone,
    ),
    "conde_auto_entry": StrategySpec(
        service_name="conde_auto_entry",
        stream="conde_signals",
        required_fields=(
            "timestamp", "symbol", "direction",
            "entry_price", "sl", "tps",
            "channel_id", "channel_name",
        ),
        optional_fields=(),
        flatten=_flatten_conde,
    ),
    "gvfx_signal": StrategySpec(
        service_name="gvfx_signal",
        stream="gvfx_signals",
        required_fields=(
            "timestamp", "symbol", "target", "direction", "step", "tp",
        ),
        optional_fields=("low", "high", "use_atr"),
        flatten=_flatten_gvfx,
    ),
}


def get_spec(service_name: str) -> StrategySpec | None:
    return REGISTRY.get(service_name)


def known_streams_help() -> str:
    """Human-readable summary used when a Bad message comes from a service
    not in the registry (no automatic republish path). The user can fall back
    to one of the existing wizards."""
    lines = [f"- {s.service_name} → {s.stream}" for s in REGISTRY.values()]
    return "\n".join(lines)
