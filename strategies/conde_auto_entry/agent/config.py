"""Load and validate settings from environment / .env file."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List

from dotenv import load_dotenv

load_dotenv()

@dataclass
class Settings:
    # Required
    mt5_signal_dir: Path        # full dir where {account}_{symbol}.json files are written
    mt5_accounts: List[int]     # e.g. [5100000, 5100001]
    mt5_symbols: List[str]      # e.g. ["XAUUSD", "EURUSD"]

    # Optional with defaults
    redis_url: str = "redis://localhost:6379"
    redis_stream: str = "conde_signals"
    redis_group: str = "conde_writer"
    redis_consumer: str = "conde-agent-1"
    log_level: str = "INFO"

    # Phase 3 — per-account channel gate
    account_config_dir: Path = Path(__file__).resolve().parent.parent / "config" / "accounts"
    stats_quality_url: str = "https://stats.auto-trade.life/conde/quality.json"
    approved_refresh_sec: int = 300

    def signal_path(self, account_id: int, symbol: str) -> Path:
        return self.mt5_signal_dir / f"{account_id}_{symbol}.json"


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

    symbols_raw = os.environ.get("MT5_SYMBOLS", "XAUUSD")
    symbols = [s.strip().upper() for s in symbols_raw.split(",") if s.strip()]
    if not symbols:
        raise RuntimeError("MT5_SYMBOLS must contain at least one symbol")

    default_cfg_dir = Path(__file__).resolve().parent.parent / "config" / "accounts"
    account_config_dir = Path(os.environ.get("CONDE_ACCOUNT_CONFIG_DIR", str(default_cfg_dir)))

    return Settings(
        mt5_signal_dir=Path(signal_dir),
        mt5_accounts=accounts,
        mt5_symbols=symbols,
        redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379"),
        redis_stream=os.environ.get("REDIS_STREAM", "conde_signals"),
        redis_group=os.environ.get("REDIS_GROUP", "conde_writer"),
        redis_consumer=os.environ.get("REDIS_CONSUMER", "conde-agent-1"),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        account_config_dir=account_config_dir,
        stats_quality_url=os.environ.get(
            "STATS_QUALITY_URL", "https://stats.auto-trade.life/conde/quality.json"
        ),
        approved_refresh_sec=int(os.environ.get("APPROVED_REFRESH_SEC", "300")),
    )
