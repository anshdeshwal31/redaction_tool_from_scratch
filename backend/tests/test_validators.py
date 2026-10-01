"""Published / hand-computed test vectors for the identifier validators (plan §12)."""

from __future__ import annotations

import pytest

from redactor.detectors.baseline import validators as v


@pytest.mark.parametrize("value", ["123 456 782", "123456782"])
def test_tfn_valid(value):
    assert v.tfn(value)


@pytest.mark.parametrize("value", ["123 456 789", "12345678", "1234567890", ""])
def test_tfn_invalid(value):
    assert not v.tfn(value)


def test_abn():
    assert v.abn("51 824 753 556")  # the ATO's published ABN
    assert not v.abn("51 824 753 557")
    assert not v.abn("01 824 753 556")


def test_acn():
    assert v.acn("004 085 616")
    assert v.acn("000 000 019")
    assert not v.acn("004 085 617")


def test_medicare():
    assert v.medicare("2123 45670 1")
    assert v.medicare("2123456701")
    assert not v.medicare("2123 45671 1")
    assert not v.medicare("7123 45670 1")  # first digit must be 2-6


def test_provider_number():
    assert v.provider_number("2429581T")
    assert v.provider_number("242958 1T")
    assert not v.provider_number("2429581X")
    assert v.provider_check_char("242958", "1") == "T"


def _luhn_complete(prefix: str) -> str:
    for d in "0123456789":
        if v.luhn(prefix + d):
            return prefix + d
    raise AssertionError


def test_ihi():
    number = _luhn_complete("800360" + "123456789")
    assert v.ihi(number)
    bad = number[:-1] + str((int(number[-1]) + 1) % 10)
    assert not v.ihi(bad)
    assert not v.ihi(_luhn_complete("800361" + "123456789"))  # HPI-I prefix, not an IHI


def test_any_valid():
    assert v.any_valid("123 456 782") == ["tfn"]
    assert v.any_valid("hello") == []
