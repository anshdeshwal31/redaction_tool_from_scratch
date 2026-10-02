"""Keyed derivation (plan §4.9.1): HMAC-SHA256(matter key, tag | normalised value) indexes surrogate
pools; nobody without the matter key can recompute a surrogate from a guessed real value."""

from __future__ import annotations

import hashlib
import hmac
import string
import unicodedata

TEST_MATTER_KEY = hashlib.sha256(b"redactor evaluation test key v1 - never used in production").digest()


def norm(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def hmac_hex(key: bytes, *parts: str) -> str:
    return hmac.new(key, "\x1f".join(parts).encode("utf-8"), hashlib.sha256).hexdigest()


def hmac_int(key: bytes, *parts: str) -> int:
    return int(hmac_hex(key, *parts)[:16], 16)


def pick(key: bytes, pool: list[str], *parts: str, taken: set[str] | None = None, attempt_limit: int = 10_000) -> tuple[str, int]:
    """Deterministic choice from `pool`; on collision with `taken`, retry with a counter. Returns (value, counter)."""
    if not pool:
        raise ValueError("empty surrogate pool")
    for c in range(attempt_limit):
        v = pool[hmac_int(key, *parts, str(c)) % len(pool)]
        if taken is None or v not in taken:
            return v, c
        if len(taken) >= len(set(pool)):
            break
    raise ValueError("surrogate pool exhausted")


def letter_permutation(key: bytes) -> dict[str, str]:
    """A per-matter permutation of A-Z (initials map consistently, §4.9.1)."""
    letters = list(string.ascii_uppercase)
    ranked = sorted(letters, key=lambda ch: hmac_hex(key, "INITIAL", ch))
    return dict(zip(letters, ranked))
