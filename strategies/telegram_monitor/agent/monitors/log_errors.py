"""Scan agent logs for error-shaped lines and alert.

Reads only new bytes since last tick (per-service offset). On rotation
the transport resets to start. Patterns are intentionally narrow — they
catch Python tracebacks and explicit ERROR/CRITICAL log lines, not the
word "error" appearing in normal output.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from ..alerts import AlertDispatcher
from ..config import Settings
from ..transports import Transport
from .badmsg_parser import parse_bad_message

log = logging.getLogger(__name__)

BADMSG_TTL_S = 86400  # 24h — long enough to survive a weekend on-call window

PATTERNS = [
    re.compile(r"^\s*Traceback \(most recent call last\):", re.MULTILINE),
    re.compile(r"\b(ERROR|CRITICAL|FATAL)\b"),
    re.compile(r"\bUnhandled exception\b", re.IGNORECASE),
]
# Lines whose presence makes the whole snippet "expected shutdown noise" —
# CI deploy stops services with Ctrl+C-equivalent, which surfaces as a
# KeyboardInterrupt traceback. Not a real error.
BENIGN_PATTERNS = [
    re.compile(r"^KeyboardInterrupt\b", re.MULTILINE),
    re.compile(r"^SystemExit\b", re.MULTILINE),
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
    redis = ctx.get("redis")  # optional: enables [✏ Edit] button on Bad message alerts

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
            if any(p.search(new_data) for p in BENIGN_PATTERNS):
                # Deploy-time SIGINT etc. — log locally but don't page.
                log.info("log scan: benign shutdown trace skipped (%s/%s)",
                         vps.name, svc.name)
                continue
            snippet = new_data[-MAX_SNIPPET:]
            # Parse over the full new_data, not the truncated snippet — the
            # `raw=...` dict can exceed MAX_SNIPPET. Snippet is for display only.
            reply_markup = await _maybe_edit_markup(redis, vps.name, svc.name, new_data)
            await alerts.notify(
                dedup_key=f"log_err:{vps.name}:{svc.name}:{_hash(snippet)}",
                text=(
                    f"⚠️ *{svc.name}* — error in `{active.name}`\n"
                    f"```\n{snippet}\n```"
                ),
                reply_markup=reply_markup,
            )
        except Exception as e:
            log.warning("log scan failed (%s/%s): %s", vps.name, svc.name, e)


async def _maybe_edit_markup(redis, vps_name: str, svc_name: str, full_chunk: str):
    """Build an `[✏ Edit]` keyboard if `full_chunk` contains a parseable
    Bad message line. Returns None when no badmsg pattern is found, when the
    Redis client is unavailable, or when the cache write fails (the alert
    still goes out; only the button is dropped).
    """
    if redis is None:
        return None
    bad = parse_bad_message(full_chunk)
    if bad is None:
        return None
    token = secrets.token_urlsafe(8)
    record = json.dumps({
        "service": svc_name,
        "vps": vps_name,
        "msg_id": bad.msg_id,
        "exc": bad.exc,
        "payload": bad.payload,
    })
    try:
        await redis.setex(f"badmsg:{token}", BADMSG_TTL_S, record)
    except Exception as e:
        log.warning("badmsg cache write failed (%s/%s): %s", vps_name, svc_name, e)
        return None
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✏ Edit", callback_data=f"badmsg:edit:{token}"),
    ]])
