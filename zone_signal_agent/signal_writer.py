"""Atomic signal file writer — one instance per MT5 account."""

import logging
import os
import time
from pathlib import Path

from models import ZoneSignal

log = logging.getLogger(__name__)


class SignalWriter:
    """
    Writes ZoneSignal to `{base_dir}/{account_id}.json` atomically.

    Write flow:
        1. Stamp a fresh timestamp onto the signal.
        2. Serialize to JSON and write to `{account_id}.tmp`.
        3. os.replace(tmp, target) — atomic rename at the OS level.

    The EA always sees either the complete old file or the complete new file,
    never a partial write.  No explicit file locking is needed.

    On Windows, MT5 may briefly hold the target file open while reading.
    A single retry after 50 ms handles that edge case.
    """

    def __init__(self, account_id: int, base_dir: Path) -> None:
        self.account_id = account_id
        self.signal_path = base_dir / f"{account_id}.json"
        self._tmp_path = self.signal_path.with_suffix(".tmp")

    def write(self, sig: ZoneSignal) -> None:
        sig.validate()
        sig.timestamp = int(time.time())   # always stamp at write time

        self._tmp_path.write_text(sig.to_json(), encoding="ascii")

        for attempt in range(2):
            try:
                os.replace(self._tmp_path, self.signal_path)
                log.debug("[%s] atomic rename OK (ts=%d)", self.account_id, sig.timestamp)
                return
            except PermissionError:
                if attempt == 0:
                    # EA briefly has the file open — wait one tick and retry
                    time.sleep(0.05)
                else:
                    raise
