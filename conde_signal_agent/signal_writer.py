"""Atomic signal file writer — one instance per (MT5 account, symbol) pair."""

import logging
import os
import time
from pathlib import Path

from models import CondeSignal

log = logging.getLogger(__name__)


class SignalWriter:
    """
    Writes CondeSignal to `{base_dir}/CondeAutoEntryEA/{account_id}_{symbol}.json`
    atomically.

    Write flow:
        1. Serialize to JSON (timestamp preserved as-received — NOT re-stamped).
        2. Write to `{account_id}_{symbol}.tmp`.
        3. os.replace(tmp, target) — atomic rename at the OS level.

    The EA uses sig.timestamp as part of its per-TP dedup comment (CAE_T{n}_{ts}),
    so the producer-supplied timestamp must be preserved end-to-end.
    """

    def __init__(self, account_id: int, symbol: str, base_dir: Path) -> None:
        self.account_id = account_id
        self.symbol = symbol
        self.signal_path = base_dir / f"{account_id}_{symbol}.json"
        self._tmp_path = self.signal_path.with_suffix(".tmp")

    def write(self, sig: CondeSignal) -> None:
        sig.validate()

        if sig.symbol != self.symbol:
            raise ValueError(
                f"symbol mismatch: writer={self.symbol} signal={sig.symbol}"
            )

        self._tmp_path.write_text(sig.to_json(), encoding="ascii")

        for attempt in range(2):
            try:
                os.replace(self._tmp_path, self.signal_path)
                log.debug(
                    "[%s/%s] atomic rename OK (ts=%d)",
                    self.account_id, self.symbol, sig.timestamp,
                )
                return
            except PermissionError:
                if attempt == 0:
                    time.sleep(0.05)
                else:
                    raise
