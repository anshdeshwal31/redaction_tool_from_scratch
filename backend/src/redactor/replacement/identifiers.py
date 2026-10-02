"""Identifier surrogates (plan §4.9.3).

- MEDICARE, ABN, ACN, PROVIDER_NUMBER, IHI: same length, grouping and leading-digit rules, digits from
  the HMAC, then the check digit is forced to be INVALID with the same validators the baseline uses. A
  checksum-valid identifier in any output is therefore an automatic leak alarm.
- Every other identifier: format-preserving (digits -> digits, letters -> letters of the same case,
  separators kept), collision-checked against the vault and against every identifier-like token of
  the matter's text. TFN never gets a surrogate: it is REDACT -> [TFN].
"""

from __future__ import annotations

import re
import string

from ..detectors.baseline import validators as v
from .keys import hmac_hex, norm

CHECKSUMMED = {"MEDICARE": "medicare", "ABN": "abn", "ACN": "acn", "PROVIDER_NUMBER": "provider_number", "IHI": "ihi"}


def _stream(key: bytes, *parts: str):
    i = 0
    while True:
        for ch in hmac_hex(key, *parts, str(i)):
            yield int(ch, 16)
        i += 1


def format_preserving(key: bytes, kind: str, value: str, attempt: int = 0) -> str:
    s = _stream(key, "FP", kind, norm(value), str(attempt))
    out = []
    for ch in value:
        if ch.isdigit():
            out.append(str(next(s) % 10))
        elif ch.isalpha() and ch.isascii():
            letters = string.ascii_uppercase if ch.isupper() else string.ascii_lowercase
            out.append(letters[(next(s) * 16 + next(s)) % 26])
        else:
            out.append(ch)
    return "".join(out)


def _put_digits(template: str, digits: str) -> str:
    it = iter(digits)
    return "".join(next(it) if ch.isdigit() else ch for ch in template)


def checksum_invalid(key: bytes, kind: str, value: str, attempt: int = 0) -> str:
    validator = CHECKSUMMED[kind]
    d = v.digits_only(value)
    s = _stream(key, "CI", kind, norm(value), str(attempt))
    if kind == "PROVIDER_NUMBER":
        core = re.sub(r"[^0-9A-Za-z]", "", value).upper()
        if len(core) != 8 or not core[:6].isdigit():
            return format_preserving(key, kind, value, attempt)
        stem = "".join(str(next(s) % 10) for _ in range(6))
        loc = v.PROVIDER_LOCATION_CHARS[next(s) % len(v.PROVIDER_LOCATION_CHARS)]
        good = v.provider_check_char(stem, loc)
        bad = [c for c in v.PROVIDER_CHECK_CHARS if c != good]
        chk = bad[next(s) % len(bad)]
        new = stem + loc + chk
        it = iter(new)
        return "".join((next(it) if ch.isalnum() else ch) for ch in value) if sum(c.isalnum() for c in value) == 8 else new
    digits = [str(next(s) % 10) for _ in d]
    if kind == "MEDICARE" and digits:
        digits[0] = "23456"[next(s) % 5]
    if kind == "ABN" and digits:
        digits[0] = str(1 + next(s) % 9)
    if kind == "IHI" and len(digits) == 16:
        digits[:6] = list("800360")
    cand = "".join(digits)
    for _ in range(20):
        if not v.validate(validator, cand):
            break
        pos = 8 if kind == "MEDICARE" and len(cand) > 8 else len(cand) - 1
        cand = cand[:pos] + str((int(cand[pos]) + 1) % 10) + cand[pos + 1:]
    return _put_digits(value, cand)


def surrogate(key: bytes, kind: str, value: str, *, taken: set[str], forbidden_digits: set[str]) -> str:
    """Collision-checked surrogate: never equal to an issued surrogate or to any identifier-like token of the matter."""
    for attempt in range(1000):
        cand = checksum_invalid(key, kind, value, attempt) if kind in CHECKSUMMED else format_preserving(key, kind, value, attempt)
        dg = v.digits_only(cand)
        if cand in taken or (dg and dg in forbidden_digits) or norm(cand) == norm(value):
            continue
        return cand
    raise ValueError("no free identifier surrogate")


def identifier_tokens(texts) -> set[str]:
    """Digit runs (separators allowed) of 5+ digits in the matter's text: surrogates must avoid them."""
    out: set[str] = set()
    for t in texts:
        for m in re.finditer(r"\d[\d \-/]{3,}\d", t):
            d = v.digits_only(m.group(0))
            if len(d) >= 5:
                out.add(d)
    return out
