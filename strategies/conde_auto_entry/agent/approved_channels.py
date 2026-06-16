"""Live operator-approved channel set, fetched from strategy-stats (Phase 3).

Source of truth for ``conde_channel_filter: approved``. Polls
``STATS_QUALITY_URL`` (the Phase 2 ``/conde/quality.json`` endpoint) in a
background thread and keeps the last-known-good set of APPROVED channel_ids.

Fail behaviour (operator choice):
- transient fetch error ⇒ keep the last successful set (last-known-good).
- never fetched successfully yet ⇒ ``ready`` is False; the gate treats
  ``approved`` as allow-all (fail-open) + WARN so a stats outage at startup
  doesn't halt all trading.
"""
from __future__ import annotations

import logging
import threading
import time

import httpx

log = logging.getLogger(__name__)


class ApprovedChannels:
    def __init__(self, url: str, refresh_sec: int = 300, timeout: float = 10.0) -> None:
        self._url = url
        self._refresh = max(30, int(refresh_sec))
        self._timeout = timeout
        self._lock = threading.Lock()
        self._approved: frozenset[int] = frozenset()
        self._ready = False
        self._last_ok = 0.0

    @property
    def ready(self) -> bool:
        with self._lock:
            return self._ready

    def approved(self) -> frozenset[int]:
        with self._lock:
            return self._approved

    def refresh_once(self) -> bool:
        try:
            resp = httpx.get(self._url, timeout=self._timeout)
            resp.raise_for_status()
            payload = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            with self._lock:
                kept = len(self._approved)
            log.warning(
                "approved fetch failed (%s) — keeping last-known-good (%d ids, ready=%s)",
                exc, kept, self._ready,
            )
            return False

        ids: set[int] = set()
        for c in payload.get("channels", []):
            if str(c.get("verdict", "")).upper() != "APPROVED":
                continue
            cid = c.get("channel_id")
            if cid is None:
                continue
            try:
                ids.add(int(cid))
            except (TypeError, ValueError):
                continue

        with self._lock:
            self._approved = frozenset(ids)
            self._ready = True
            self._last_ok = time.time()
        log.info("approved channels refreshed: %d", len(ids))
        return True

    def run_forever(self) -> None:
        """Background loop — refresh immediately, then every refresh_sec."""
        while True:
            self.refresh_once()
            time.sleep(self._refresh)
