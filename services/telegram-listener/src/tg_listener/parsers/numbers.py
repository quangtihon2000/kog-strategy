"""Number normalization helper for channel parsers. Spec section 5.4.

Handles comma thousands separators and k/M magnitude suffixes.
"""

from __future__ import annotations


def _norm_num(s: str) -> float:
    """Normalize a numeric string to float.

    Accepts:
    - Plain integers and floats: "67500", "3.14"
    - Comma thousands separators: "67,500", "1,200.50"
    - Magnitude suffixes (case-insensitive): "3.5k" → 3500.0, "1.2M" → 1_200_000.0
    - Leading/trailing whitespace is stripped.

    Raises:
        ValueError: if the string is empty, non-numeric, or ambiguous (e.g. "1.2.3").
    """
    cleaned = s.strip().lower().replace(",", "")
    if not cleaned:
        raise ValueError(f"empty numeric string: {s!r}")

    multiplier = 1.0
    if cleaned.endswith("k"):
        multiplier = 1_000.0
        cleaned = cleaned[:-1]
    elif cleaned.endswith("m"):
        multiplier = 1_000_000.0
        cleaned = cleaned[:-1]

    if not cleaned:
        raise ValueError(f"no digits after suffix removal: {s!r}")

    try:
        return float(cleaned) * multiplier
    except ValueError:
        raise ValueError(f"cannot parse as number: {s!r}") from None
