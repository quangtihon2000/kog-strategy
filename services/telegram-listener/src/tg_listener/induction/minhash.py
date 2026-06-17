"""Minimal MinHash for near-duplicate text detection.

Uses character k-shingling (k=5) and m permutation hashes (m=64). Jaccard
similarity is estimated as the fraction of matching positions in the
fingerprint vectors. Two fingerprints with similarity >= threshold are
considered near-duplicates.

Permutation hashes: precomputed (a, b) coefficients with a fixed seed so the
fingerprints are reproducible across processes (important for tests).
"""

from __future__ import annotations

import hashlib
import re
from typing import Final

K: Final = 5
M: Final = 64
_LARGE_PRIME: Final = (1 << 61) - 1  # Mersenne prime
_MAX_HASH: Final = (1 << 32) - 1

# Deterministic permutation coefficients.
_SEED = b"tg_listener.induction.minhash:v1"
_PERMUTATIONS: list[tuple[int, int]] = []


def _init_permutations() -> None:
    if _PERMUTATIONS:
        return
    blob = b""
    counter = 0
    while len(blob) < 8 * M:
        blob += hashlib.sha256(_SEED + counter.to_bytes(4, "big")).digest()
        counter += 1
    for i in range(M):
        a = int.from_bytes(blob[8 * i : 8 * i + 4], "big") | 1  # ensure odd → coprime w/ 2
        b = int.from_bytes(blob[8 * i + 4 : 8 * i + 8], "big")
        _PERMUTATIONS.append((a, b))


def _shingles(text: str) -> set[str]:
    """Lowercase + collapse whitespace + char k-shingles."""
    cleaned = re.sub(r"\s+", " ", text.strip().lower())
    if len(cleaned) < K:
        return {cleaned} if cleaned else set()
    return {cleaned[i : i + K] for i in range(len(cleaned) - K + 1)}


def fingerprint(text: str) -> tuple[int, ...]:
    """Compute MinHash fingerprint as a tuple of M ints."""
    _init_permutations()
    shingles = _shingles(text)
    if not shingles:
        return tuple(_MAX_HASH for _ in range(M))

    shingle_hashes = [
        int.from_bytes(hashlib.sha1(s.encode()).digest()[:4], "big") for s in shingles
    ]

    out: list[int] = []
    for a, b in _PERMUTATIONS:
        out.append(min(((a * h + b) % _LARGE_PRIME) & _MAX_HASH for h in shingle_hashes))
    return tuple(out)


def jaccard(fp_a: tuple[int, ...], fp_b: tuple[int, ...]) -> float:
    """Estimate Jaccard similarity from two fingerprints."""
    if len(fp_a) != len(fp_b):
        raise ValueError("fingerprint length mismatch")
    if not fp_a:
        return 1.0
    matches = sum(1 for x, y in zip(fp_a, fp_b, strict=True) if x == y)
    return matches / len(fp_a)
