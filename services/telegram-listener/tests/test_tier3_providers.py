"""Tests for Tier 3 provider abstraction, factory, and LLMResponse model."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tg_listener.tiers.llm.base import LLMProvider, LLMResponse, ProviderError
from tg_listener.tiers.llm.factory import LLMSettings, make_provider
from tg_listener.tiers.llm.stub import StubLLMProvider

# ---------------------------------------------------------------------------
# Protocol / runtime_checkable
# ---------------------------------------------------------------------------


def test_stub_satisfies_llm_provider_protocol() -> None:
    stub = StubLLMProvider([])
    assert isinstance(stub, LLMProvider)


def test_plain_object_does_not_satisfy_protocol() -> None:
    class NotAProvider:
        pass

    assert not isinstance(NotAProvider(), LLMProvider)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def test_make_provider_stub_succeeds() -> None:
    settings = LLMSettings(provider="stub")  # type: ignore[call-arg]
    provider = make_provider("stub", settings)
    assert isinstance(provider, StubLLMProvider)


def test_make_provider_unknown_raises_value_error() -> None:
    settings = LLMSettings()
    with pytest.raises(ValueError, match="unknown LLM provider"):
        make_provider("nonexistent", settings)


def test_make_provider_anthropic_without_key_raises() -> None:
    settings = LLMSettings(provider="anthropic", anthropic_api_key=None)  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="TIER3_ANTHROPIC_API_KEY"):
        make_provider("anthropic", settings)


def test_make_provider_anthropic_with_key_succeeds() -> None:
    from tg_listener.tiers.llm.anthropic import AnthropicProvider

    settings = LLMSettings(provider="anthropic", anthropic_api_key="sk-test")  # type: ignore[call-arg]
    provider = make_provider("anthropic", settings)
    assert isinstance(provider, AnthropicProvider)


def test_make_provider_ollama_succeeds() -> None:
    from tg_listener.tiers.llm.ollama import OllamaProvider

    settings = LLMSettings(provider="ollama")  # type: ignore[call-arg]
    provider = make_provider("ollama", settings)
    assert isinstance(provider, OllamaProvider)


# ---------------------------------------------------------------------------
# LLMResponse validation
# ---------------------------------------------------------------------------


def test_llm_response_valid_signal() -> None:
    resp = LLMResponse.model_validate(
        {
            "is_signal": True,
            "symbol": "XAUUSD",
            "side": "LONG",
            "entry": 2350.0,
            "sl": 2330.0,
            "tp": [2370.0, 2390.0],
            "leverage": 10,
            "confidence": 0.85,
        },
        strict=False,
    )
    assert resp.is_signal is True
    assert resp.symbol == "XAUUSD"
    assert resp.confidence == 0.85


def test_llm_response_not_signal() -> None:
    resp = LLMResponse.model_validate({"is_signal": False}, strict=False)
    assert resp.is_signal is False
    assert resp.symbol is None


def test_llm_response_rejects_bad_side() -> None:
    with pytest.raises(ValidationError):
        LLMResponse.model_validate(
            {"is_signal": True, "side": "NEUTRAL"},
            strict=False,
        )


def test_llm_response_allows_leverage_none() -> None:
    resp = LLMResponse.model_validate(
        {"is_signal": True, "leverage": None},
        strict=False,
    )
    assert resp.leverage is None


def test_llm_response_extra_fields_ignored() -> None:
    resp = LLMResponse.model_validate(
        {"is_signal": False, "unexpected_key": "boom"},
        strict=False,
    )
    assert resp.is_signal is False


def test_llm_response_missing_is_signal_raises() -> None:
    with pytest.raises(ValidationError):
        LLMResponse.model_validate({"symbol": "XAUUSD"}, strict=False)


def test_provider_error_is_exception() -> None:
    err = ProviderError("http 500")
    assert isinstance(err, Exception)
    assert "500" in str(err)
