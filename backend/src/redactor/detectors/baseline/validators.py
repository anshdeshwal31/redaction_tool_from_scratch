"""Checksum validators for Australian identifiers (plan §7.1).

Reused by the baseline detector, `dataset validate`, the surrogate generator (to force invalid
surrogates, §4.9.3) and the final leak scan (§4.11).
"""

from __future__ import annotations

import re

_NON_DIGIT = re.compile(r"\D")
_NON_ALNUM = re.compile(r"[^0-9A-Za-z]")

PROVIDER_LOCATION_CHARS = "0123456789ABCDEFGHJKLMNPQRTUVWXY"
PROVIDER_CHECK_CHARS = "YXWTLKJHFBA"
TFN_WEIGHTS_9 = (1, 4, 3, 7, 5, 8, 6, 9, 10)
TFN_WEIGHTS_8 = (10, 7, 8, 4, 6, 3, 5, 1)
ABN_WEIGHTS = (10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19)
ACN_WEIGHTS = (8, 7, 6, 5, 4, 3, 2, 1)
MEDICARE_WEIGHTS = (1, 3, 7, 9, 1, 3, 7, 9)
PROVIDER_WEIGHTS = (3, 5, 8, 4, 2, 1)


def digits_only(value: str) -> str:
    return _NON_DIGIT.sub("", value)


def tfn(value: str) -> bool:
    d = digits_only(value)
    if len(d) == 9:
        weights = TFN_WEIGHTS_9
    elif len(d) == 8:
        weights = TFN_WEIGHTS_8
    else:
        return False
    return sum(int(c) * w for c, w in zip(d, weights)) % 11 == 0


def abn(value: str) -> bool:
    d = digits_only(value)
    if len(d) != 11 or d[0] == "0":
        return False
    nums = [int(c) for c in d]
    nums[0] -= 1
    return sum(n * w for n, w in zip(nums, ABN_WEIGHTS)) % 89 == 0


def acn_check_digit(first8: str) -> int:
    rem = sum(int(c) * w for c, w in zip(first8, ACN_WEIGHTS)) % 10
    return (10 - rem) % 10


def acn(value: str) -> bool:
    d = digits_only(value)
    return len(d) == 9 and acn_check_digit(d[:8]) == int(d[8])


def medicare_check_digit(first8: str) -> int:
    return sum(int(c) * w for c, w in zip(first8, MEDICARE_WEIGHTS)) % 10


def medicare(value: str) -> bool:
    d = digits_only(value)
    if len(d) not in (10, 11) or d[0] not in "23456":
        return False
    return medicare_check_digit(d[:8]) == int(d[8])


def provider_check_char(stem6: str, location: str) -> str:
    loc = PROVIDER_LOCATION_CHARS.index(location.upper())
    total = sum(int(c) * w for c, w in zip(stem6, PROVIDER_WEIGHTS)) + loc * 6
    return PROVIDER_CHECK_CHARS[total % 11]


def provider_number(value: str) -> bool:
    v = _NON_ALNUM.sub("", value).upper()
    if len(v) != 8 or not v[:6].isdigit():
        return False
    if v[6] not in PROVIDER_LOCATION_CHARS or v[7] not in PROVIDER_CHECK_CHARS:
        return False
    return provider_check_char(v[:6], v[6]) == v[7]


def luhn(value: str) -> bool:
    d = digits_only(value)
    if not d:
        return False
    total = 0
    for i, c in enumerate(reversed(d)):
        n = int(c)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def ihi(value: str) -> bool:
    d = digits_only(value)
    return len(d) == 16 and d.startswith("800360") and luhn(d)


VALIDATORS = {
    "tfn": tfn,
    "abn": abn,
    "acn": acn,
    "medicare": medicare,
    "provider_number": provider_number,
    "ihi": ihi,
}


def validate(kind: str, value: str) -> bool:
    return VALIDATORS[kind](value)


def any_valid(value: str) -> list[str]:
    """Names of every validator the value passes (used by the leak scan)."""
    return [name for name, fn in VALIDATORS.items() if fn(value)]
