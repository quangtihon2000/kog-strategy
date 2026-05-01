"""Load and validate settings from environment / .env file."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    # Required
    mt5_signal_dir: Path        # directory where {account}.json files are written
    mt5_accounts: List[int]     # e.g. [5100000, 5100001]

    # Optional with defaults
    redis_url: str = "redis://localhost:6379"
    redis_stream: str = "zone_signals"
    redis_group: str = "ea_writer"
    redis_consumer: str = "agent-1"
    log_level: str = "INFO"

    def signal_path(self, account_id: int) -> Path:
        return self.mt5_signal_dir / f"{account_id}.json"


def load_settings() -> Settings:
    signal_dir = os.environ.get("MT5_SIGNAL_DIR", "")
    if not signal_dir:
        raise RuntimeError("MT5_SIGNAL_DIR environment variable is required")

    accounts_raw = os.environ.get("MT5_ACCOUNTS", "")
    if not accounts_raw:
        raise RuntimeError("MT5_ACCOUNTS environment variable is required")

    accounts = [int(a.strip()) for a in accounts_raw.split(",") if a.strip()]
    if not accounts:
        raise RuntimeError("MT5_ACCOUNTS must contain at least one account id")

    return Settings(
        mt5_signal_dir=Path(signal_dir),
        mt5_accounts=accounts,
        redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379"),
        redis_stream=os.environ.get("REDIS_STREAM", "zone_signals"),
        redis_group=os.environ.get("REDIS_GROUP", "ea_writer"),
        redis_consumer=os.environ.get("REDIS_CONSUMER", "agent-1"),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
    )
