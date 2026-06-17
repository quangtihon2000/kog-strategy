"""Configuration for tg_listener.

Two layers:

1. **Process config** — env vars loaded via pydantic-settings (`Settings`). Spec 5.1.
2. **Channel config** — YAML at `SIGNAL_CHANNELS_CONFIG` loaded into typed
   `ChannelsConfig`. Spec section 8.

Both are validated in strict mode and frozen so callers cannot mutate live config.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from .exceptions import ChannelsConfigError, ConfigError


class Settings(BaseSettings):
    """Process-level config from env vars. Spec 5.1."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    telegram_api_id: int
    telegram_api_hash: str
    telegram_session_name: str = "kog_signals"
    telegram_session_dir: Path = Path("/data/sessions")

    signal_channels_config: Path = Path("/etc/kog/channels.yaml")

    redis_url: str = "redis://redis:6379/0"

    llm_provider: Literal["anthropic", "google"] = "anthropic"
    anthropic_api_key: SecretStr | None = None
    google_api_key: str | None = None

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    startup_replay_window_min: int = Field(default=1, ge=0)


# --- channels.yaml schema -------------------------------------------------


class ChannelConfig(BaseModel):
    """One entry under `channels:` in channels.yaml. Spec section 8."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    id: int
    name: str
    enabled: bool = True
    type: Literal["broadcast", "group"] = "broadcast"
    allow_forward: bool = False
    parser: str
    admin_user_ids: list[int] = Field(default_factory=list)
    notes: str | None = None


class LLMConfig(BaseModel):
    """`llm:` block in channels.yaml. Spec section 8."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    provider: Literal["anthropic", "google"] = "anthropic"
    model: str = "claude-haiku-4-5-20251001"
    timeout_s: float = Field(default=5.0, gt=0)
    max_retries: int = Field(default=2, ge=0)
    confidence_threshold: float = Field(default=0.7, ge=0.0, le=1.0)


class FilteringConfig(BaseModel):
    """`filtering:` block in channels.yaml. Spec section 8."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    startup_replay_window_min: int = Field(default=1, ge=0)
    rejected_sample_rate: float = Field(default=0.005, ge=0.0, le=1.0)


class ChannelsConfig(BaseModel):
    """Top-level schema for channels.yaml. Spec section 8."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    channels: list[ChannelConfig]
    llm: LLMConfig = Field(default_factory=LLMConfig)
    filtering: FilteringConfig = Field(default_factory=FilteringConfig)

    def enabled_channel_ids(self) -> list[int]:
        return [c.id for c in self.channels if c.enabled]


def load_channels_config(path: Path | str) -> ChannelsConfig:
    """Read channels.yaml from disk and parse into `ChannelsConfig`.

    Raises:
        ChannelsConfigError: file missing, not valid YAML, or fails schema.
    """
    p = Path(path)
    if not p.is_file():
        raise ChannelsConfigError(f"channels config not found at {p}")
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ChannelsConfigError(f"channels.yaml is not valid YAML: {e}") from e
    if not isinstance(raw, dict):
        raise ChannelsConfigError("channels.yaml top level must be a mapping")
    try:
        return ChannelsConfig.model_validate(raw)
    except Exception as e:  # pydantic ValidationError
        raise ChannelsConfigError(f"channels.yaml failed schema validation: {e}") from e


def load_settings() -> Settings:
    """Load process settings from env. Raises ConfigError on validation failure."""
    try:
        return Settings()  # type: ignore[call-arg]
    except Exception as e:
        raise ConfigError(f"invalid env config: {e}") from e
