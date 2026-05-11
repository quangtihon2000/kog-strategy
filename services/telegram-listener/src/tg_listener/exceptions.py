"""Domain exceptions for the Telegram signal listener.

Spec section 16 — domain exceptions live here, never raise generic `Exception`.
"""


class TgListenerError(Exception):
    """Base class for all tg_listener domain errors."""


class ConfigError(TgListenerError):
    """Raised when the listener config (env vars or channels.yaml) is invalid."""


class ChannelsConfigError(ConfigError):
    """Raised when `channels.yaml` is missing, malformed, or fails schema check."""


class SessionCorruptError(TgListenerError):
    """Raised when the Telethon session file is corrupt or requires re-auth.

    Maps to `SessionPasswordNeededError` and similar Telethon failures (spec 11).
    """


class ParserError(TgListenerError):
    """Base class for parser-layer errors (Tier 2 & Tier 3)."""


class ParserNotFoundError(ParserError):
    """No parser registered for the given channel_id (spec 5.4 dispatcher)."""


class ParserRegexError(ParserError):
    """Regex parse failed in a way the dispatcher should swallow (spec 5.4)."""


class LLMExtractorError(ParserError):
    """Tier 3 LLM extractor failed (timeout, schema mismatch, provider error)."""


class LLMTimeoutError(LLMExtractorError):
    """Tier 3 LLM call exceeded `llm.timeout_s`."""


class ValidationFailed(TgListenerError):
    """Raised when a parsed signal fails Tier 4 semantic gates (spec 5.6).

    Carries the structured `ValidationResult.reason` so observability can label it.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class StorageError(TgListenerError):
    """Base class for storage-layer errors (Redis / Postgres)."""


class RedisUnavailable(StorageError):
    """Redis ping or XADD failed; pipeline should buffer locally (spec 11)."""
