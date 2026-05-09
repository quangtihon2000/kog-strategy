"""Settings + fleet inventory loader.

Two sources:
    - .env / process env  → bot token, allowed users, redis url, log level
    - fleet.yaml          → list of VPSes + services to monitor

Keep config dumb: the rest of the code receives a Fleet object and never
re-reads YAML or env.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

DEFAULT_FLEET_PATH = Path(__file__).parent / "fleet.yaml"


@dataclass(frozen=True)
class Mt5LogTarget:
    account: str                 # MT5 account number, used as account picker label
    log_dir: str                 # absolute path to MQL5/Logs on the VPS


@dataclass(frozen=True)
class Service:
    name: str                    # e.g. "zone_signal" — used in commands
    nssm_service: str            # e.g. "zone_signal_agent"
    agent_dir: str
    log_dir: str
    # signal_dir / signal_freshness_min are optional — services that don't
    # produce signal files (e.g. the github-actions runner) leave them unset
    # and skip signal-related views.
    signal_dir: str | None = None
    signal_freshness_min: int | None = None
    # MT5 Expert log dirs per account this service drives. /logs combines
    # them with the Python agent log: 0 → agent only, 1 → both, N → picker.
    mt5_logs: tuple[Mt5LogTarget, ...] = ()


@dataclass(frozen=True)
class Vps:
    name: str
    transport: str               # "local" (Phase 0) | "ssh" (Phase 1)
    services: list[Service]
    # Reserved for Phase 1 SSH transport
    host: str | None = None
    user: str | None = None
    key_path: str | None = None


@dataclass(frozen=True)
class Fleet:
    vpses: list[Vps]

    def find_service(self, name: str) -> tuple[Vps, Service] | None:
        """Return (vps, service) for a service name, or None.

        Phase 0: names are unique across the fleet.
        Phase 1: if collisions appear, switch to "{vps}/{service}" addressing.
        """
        for v in self.vpses:
            for s in v.services:
                if s.name == name:
                    return v, s
        return None

    def all_services(self) -> list[tuple[Vps, Service]]:
        return [(v, s) for v in self.vpses for s in v.services]


@dataclass(frozen=True)
class Settings:
    bot_token: str
    allowed_user_ids: frozenset[int]
    alert_chat_ids: frozenset[int]
    redis_url: str
    log_level: str
    fleet: Fleet
    # Base URL for the per-signal stats page. New-signal notifications append
    # `?ts=<timestamp>` and include a link in the body. Set empty to disable.
    signal_stats_url: str = "https://quangtihon2000.github.io/conde-stats/"


def _parse_chat_ids(raw: str, var_name: str) -> frozenset[int]:
    ids = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            raise RuntimeError(f"{var_name} contains non-integer: {part!r}")
    return frozenset(ids)


def load_fleet(path: Path = DEFAULT_FLEET_PATH) -> Fleet:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    vpses = []
    for v in raw.get("vpses", []):
        services = []
        for s in v.get("services", []):
            mt5_logs_raw = s.pop("mt5_logs", None) or []
            mt5_logs = tuple(Mt5LogTarget(**m) for m in mt5_logs_raw)
            services.append(Service(mt5_logs=mt5_logs, **s))
        vpses.append(Vps(
            name=v["name"],
            transport=v["transport"],
            services=services,
            host=v.get("host"),
            user=v.get("user"),
            key_path=v.get("key_path"),
        ))
    return Fleet(vpses=vpses)


def load_settings() -> Settings:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")

    allowed_raw = os.environ.get("TELEGRAM_ALLOWED_USER_IDS", "").strip()
    # Empty whitelist = bot rejects everyone (read-only safety default).
    allowed = (
        _parse_chat_ids(allowed_raw, "TELEGRAM_ALLOWED_USER_IDS")
        if allowed_raw else frozenset()
    )

    # Where AlertDispatcher fans out monitor pages. Accepts user ids (DM) or
    # group/channel chat ids (negative). Empty → fall back to allowed_user_ids
    # so existing single-operator deploys keep working without new config.
    alert_raw = os.environ.get("TELEGRAM_ALERT_CHAT_IDS", "").strip()
    alert_chat_ids = (
        _parse_chat_ids(alert_raw, "TELEGRAM_ALERT_CHAT_IDS")
        if alert_raw else allowed
    )

    fleet_path = Path(os.environ.get("FLEET_CONFIG", str(DEFAULT_FLEET_PATH)))
    fleet = load_fleet(fleet_path)

    return Settings(
        bot_token=token,
        allowed_user_ids=allowed,
        alert_chat_ids=alert_chat_ids,
        redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379"),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        fleet=fleet,
        signal_stats_url=os.environ.get(
            "TELEGRAM_SIGNAL_STATS_URL",
            "https://quangtihon2000.github.io/conde-stats/",
        ).strip(),
    )
