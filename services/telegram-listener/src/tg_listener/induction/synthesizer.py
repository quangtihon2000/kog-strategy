"""Synthesizer — derives a RegexTable from a set of labelled ParserSamples.

Flow:
  1. Build a prompt from the samples + schema explanation + worked example.
  2. Call the RegexTableSynthProvider.
  3. Validate the response via RegexTable.model_validate().
  4. On ProviderError or ValidationError, retry once with the error echoed back.
  5. On second failure, raise SynthesizerError.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import ValidationError

from tg_listener.db.seed_data import CHANNEL_A_REGEX_TABLE
from tg_listener.induction.synth_provider import RegexTableSynthProvider
from tg_listener.parsers.regex_table import RegexTable
from tg_listener.tiers.llm.base import ProviderError

log = logging.getLogger(__name__)


# ── Custom exception ──────────────────────────────────────────────────────────


class SynthesizerError(Exception):
    """Raised when the synthesizer fails to produce a valid RegexTable."""


# ── Prompt construction ───────────────────────────────────────────────────────

_SCHEMA_EXPLANATION = """
## RegexTable schema

A RegexTable is a JSON object with these fields:

- side: RegexSlot (required) — matches side keyword (LONG/SHORT/buy/sell/…)
- side_map: dict[str, "LONG"|"SHORT"] (required) — lowercase keyword → canonical
- symbol: RegexSlot | null — optional separate symbol regex
- symbol_from_side_group: int | null — capture group index in `side` that holds symbol
- entry: RegexSlot (required) — single-value entry price
- entry_zone: RegexSlot | null — zone entry (group 1 = low, group 2 = high)
- sl: RegexSlot (required) — stop-loss
- tp: RegexSlot (required) — take-profit; used with finditer, each match = one TP
- tp_split: str | null — regex to split a TP match into multiple values
- tp_comma_list: str | null — regex whose matches are replaced by space before tp_split
- leverage: RegexSlot | null — optional leverage
- pre_clean: str | null — if set, applied as re.sub(pre_clean, ' ', text) before parsing
- skip_symbols: list[str] — uppercase tokens that are NOT valid symbols

RegexSlot:
- pattern: str — raw regex string
- flags: list of "IGNORECASE"|"DOTALL"|"MULTILINE"|"UNICODE" (default: ["IGNORECASE","UNICODE"])
- group: int — which capture group to extract (default: 1)
""".strip()

_WORKED_EXAMPLE = f"""
## Worked example (Channel A)

```json
{json.dumps(CHANNEL_A_REGEX_TABLE, indent=2, ensure_ascii=False)}
```
""".strip()


def _build_prompt(samples: list[Any], retry_error: str | None = None) -> str:
    """Build the synthesis prompt from samples.

    Args:
        samples: list of ParserSample-like objects with .text and .parsed_signal.
        retry_error: validation error from the previous attempt (for retry).
    """
    # Giới hạn số lượng samples trong prompt để tránh quá dài.
    capped = samples[:50]

    samples_block_lines: list[str] = []
    for i, s in enumerate(capped, 1):
        sample_entry = {
            "text": s.text,
            "expected_output": s.parsed_signal,
        }
        entry_json = json.dumps(sample_entry, indent=2, ensure_ascii=False)
        samples_block_lines.append(f"Sample {i}:\n{entry_json}")

    samples_block = "\n\n".join(samples_block_lines)

    retry_section = ""
    if retry_error:
        retry_section = f"""
## Previous attempt failed validation

Your previous output failed validation: {retry_error}

Please fix the errors and try again.
""".strip()

    parts = [
        _SCHEMA_EXPLANATION,
        "",
        _WORKED_EXAMPLE,
        "",
        "## Your task",
        "",
        "Analyse the following trading signal samples and their expected parsed outputs.",
        "Synthesize a RegexTable that would correctly parse ALL of the samples.",
        "Return ONLY a JSON object matching the RegexTable schema.",
        "",
        "## Samples",
        "",
        samples_block,
    ]
    if retry_section:
        parts += ["", retry_section]

    return "\n".join(parts)


# ── Main synthesize function ──────────────────────────────────────────────────


async def synthesize(
    samples: list[Any],
    provider: RegexTableSynthProvider,
) -> RegexTable:
    """Synthesize a RegexTable from labelled ParserSamples.

    Args:
        samples: list of ParserSample (or duck-typed objects with .text and .parsed_signal).
                 Expects 5-50 items; fewer may produce poor results.
        provider: a RegexTableSynthProvider implementation.

    Returns:
        A validated RegexTable.

    Raises:
        SynthesizerError: if the provider fails or returns invalid output after 1 retry.
    """
    if not samples:
        raise SynthesizerError("No samples provided — cannot synthesize a RegexTable.")

    prompt = _build_prompt(samples)
    raw: dict[str, Any]

    # Lần đầu thử.
    try:
        raw = await provider.synthesize_table(prompt)
    except ProviderError as exc:
        log.warning("synthesizer_provider_error_first_attempt", extra={"error": str(exc)})
        error_msg = str(exc)
        retry_prompt = _build_prompt(samples, retry_error=error_msg)
        try:
            raw = await provider.synthesize_table(retry_prompt)
        except ProviderError as exc2:
            raise SynthesizerError(f"Provider failed on retry: {exc2}") from exc2

    # Validate lần đầu.
    try:
        return RegexTable.model_validate(raw)
    except ValidationError as val_exc:
        error_msg = str(val_exc)
        log.warning("synthesizer_validation_error_first_attempt", extra={"error": error_msg})

    # Retry với thông báo lỗi validation.
    retry_prompt = _build_prompt(samples, retry_error=error_msg)
    try:
        raw = await provider.synthesize_table(retry_prompt)
    except ProviderError as exc:
        raise SynthesizerError(f"Provider failed on retry after validation error: {exc}") from exc

    try:
        return RegexTable.model_validate(raw)
    except ValidationError as val_exc2:
        raise SynthesizerError(
            f"RegexTable validation failed after retry: {val_exc2}"
        ) from val_exc2
