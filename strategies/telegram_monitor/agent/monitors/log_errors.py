"""Scan agent logs for error-shaped lines and alert.

Reads only new bytes since last tick (per-service offset). On rotation
the transport resets to start. Patterns are intentionally narrow — they
catch Python tracebacks and explicit ERROR/CRITICAL log lines, not the
word "error" appearing in normal output.
"""

from __future__ import annotations

import hashlib
import logging
import re

from telegram.ext import ContextTypes

from ..alerts import AlertDispatcher
from ..config import Settings
from ..transports import Transport

log = logging.getLogger(__name__)

PATTERNS = [
    re.compile(r"^\s*Traceback \(most recent call last\):", re.MULTILINE),
    re.compile(r"\b(ERROR|CRITICAL|FATAL)\b"),
    re.compile(r"\bUnhandled exception\b", re.IGNORECASE),
]
MAX_SNIPPET = 800

# (vps, service, log_path) -> byte offset
_OFFSETS: dict[tuple[str, str, str], int] = {}


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()[:10]


async def tick(context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = context.job.data
    settings: Settings = ctx["settings"]
    transports: dict[str, Transport] = ctx["transports"]
    alerts: AlertDispatcher = ctx["alerts"]

    for vps, svc in settings.fleet.all_services():
        try:
            transport = transports[vps.name]
            files = await transport.list_log_files(svc.log_dir)
            if not files:
                continue
            active = files[0]
            key = (vps.name, svc.name, active.path)
            # First sight of this log file: skip (treat as baseline) so we
            # don't replay historical errors after a bot restart.
            if key not in _OFFSETS:
                _OFFSETS[key] = active.size_bytes
                continue
            new_data, new_offset = await transport.read_log_since(active.path, _OFFSETS[key])
            _OFFSETS[key] = new_offset
            if not new_data.strip():
                continue
            if not any(p.search(new_data) for p in PATTERNS):
                continue
            snippet = new_data[-MAX_SNIPPET:]
            await alerts.notify(
                dedup_key=f"log_err:{vps.name}:{svc.name}:{_hash(snippet)}",
                text=(
                    f"⚠️ *{vps.name}/{svc.name}* — error in `{active.name}`\n"
                    f"```\n{snippet}\n```"
                ),
            )
        except Exception as e:
            log.warning("log scan failed (%s/%s): %s", vps.name, svc.name, e)
