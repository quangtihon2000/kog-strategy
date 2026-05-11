"""Provider factory and settings for Tier 3 LLM extractor."""

from __future__ import annotations

from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings

from tg_listener.tiers.llm.anthropic import AnthropicProvider
from tg_listener.tiers.llm.base import LLMProvider
from tg_listener.tiers.llm.ollama import OllamaProvider
from tg_listener.tiers.llm.stub import StubLLMProvider


class LLMSettings(BaseSettings):
    """Runtime config for the Tier 3 provider. Prefix: TIER3_."""

    model_config = {"env_prefix": "TIER3_", "extra": "ignore"}

    provider: Literal["anthropic", "ollama", "stub"] = "anthropic"
    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-haiku-4-5"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"


def make_provider(name: str, settings: LLMSettings) -> LLMProvider:
    """Return a concrete provider instance for the given name."""
    if name == "anthropic":
        key = settings.anthropic_api_key
        if key is None:
            raise ValueError("TIER3_ANTHROPIC_API_KEY is required for anthropic provider")
        return AnthropicProvider(
            api_key=key.get_secret_value(),
            model=settings.anthropic_model,
        )
    if name == "ollama":
        return OllamaProvider(
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
        )
    if name == "stub":
        return StubLLMProvider(responses=[])
    raise ValueError(f"unknown LLM provider: {name!r}")
