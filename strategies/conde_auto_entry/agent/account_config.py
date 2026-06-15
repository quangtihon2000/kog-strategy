"""Per-account channel-filter config for the conde agent (Phase 3).

Reads the *same* per-account JSON the EA uses
(``strategies/conde_auto_entry/config/accounts/<account>.json``) but only the
channel-filter keys; the EA's ``Inp*`` keys are ignored here. Hot-reloads on
mtime change so an operator config push (or a dashboard-driven edit) takes
effect without restarting the agent.

Keys (all optional; absence ⇒ filter ``off`` ⇒ trade every channel):
    "conde_channel_filter"    : "off" | "approved" | "list"   (default "off")
    "conde_channel_allowlist" : [int, ...]   channel_ids, used when mode "list"
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

_VALID_MODES = ("off", "approved", "list")


@dataclass(frozen=True)
class AccountFilter:
    mode: str = "off"
    allowlist: frozenset[int] = field(default_factory=frozenset)


_OFF = AccountFilter()


class AccountConfigStore:
    """mtime-cached loader for per-account channel filters."""

    def __init__(self, config_dir: Path) -> None:
        self._dir = config_dir
        self._cache: dict[int, tuple[float, AccountFilter]] = {}  # account → (mtime, filter)
        self._lock = threading.Lock()

    def get(self, account_id: int) -> AccountFilter:
        path = self._dir / f"{account_id}.json"
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return _OFF  # no per-account file ⇒ filter off (backward compatible)

        with self._lock:
            cached = self._cache.get(account_id)
            if cached is not None and cached[0] == mtime:
                return cached[1]

        flt = self._load(path, account_id)
        with self._lock:
            self._cache[account_id] = (mtime, flt)
        return flt

    def _load(self, path: Path, account_id: int) -> AccountFilter:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("account %s: unreadable config %s (%s) — filter off", account_id, path, exc)
            return _OFF

        mode = str(data.get("conde_channel_filter", "off")).strip().lower()
        if mode not in _VALID_MODES:
            log.warning("account %s: invalid conde_channel_filter=%r — off", account_id, mode)
            mode = "off"

        allow: set[int] = set()
        for x in data.get("conde_channel_allowlist", []) or []:
            try:
                allow.add(int(x))
            except (TypeError, ValueError):
                log.warning("account %s: bad channel id %r in allowlist — skipped", account_id, x)

        flt = AccountFilter(mode=mode, allowlist=frozenset(allow))
        log.info(
            "account %s: channel filter mode=%s allowlist=%d", account_id, mode, len(allow)
        )
        return flt
