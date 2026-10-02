"""R1 replacement on synthetic data (plan §12): DOB properties, identifier safety, NATIONALITY, names, vault."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from redactor.core.types import DocumentText
from redactor.detectors.base import create, run_detector
from redactor.detectors.baseline import validators as v
from redactor.replacement import contact, dob, identifiers
from redactor.replacement.engine import output_digest, pseudonymize
from redactor.replacement.keys import TEST_MATTER_KEY
from redactor.replacement.names import NameMapper, parse
from redactor.replacement.pools import load_pools
from redactor.replacement.vault import Vault, VaultError
from redactor.taxonomy import load_policy, load_taxonomy

from test_baseline import page_from_lines

KEY = TEST_MATTER_KEY
POLICY, TAX = load_policy(), load_taxonomy()
dates = st.dates(min_value=dt.date(1930, 1, 1), max_value=dt.date(2005, 12, 31))


# ---------------------------------------------------------------- DOB (§4.9.2)
@settings(max_examples=150, deadline=None)
@given(dates, st.lists(st.integers(min_value=1, max_value=60 * 365), min_size=0, max_size=4))
def test_dob_surrogate_preserves_age_and_year(b, offsets):
    refs = [b + dt.timedelta(days=o) for o in offsets]
    s = dob.issue(KEY, "p1", b, refs, min_window_days=1)
    if s is None:
        assert len(dob.feasible(b, refs)) < 1
        return
    assert s != b and s.year == b.year
    assert not (s.month == 2 and s.day == 29)
    assert all(dob.age(s, r) == dob.age(b, r) for r in refs)
    assert dob.issue(KEY, "p1", b, refs, min_window_days=1) == s   # deterministic


def test_leap_day_birthdays():
    b = dt.date(1972, 2, 29)
    assert dob.age(b, dt.date(2023, 2, 28)) == 50 and dob.age(b, dt.date(2023, 3, 1)) == 51
    assert dob.age(b, dt.date(2024, 2, 29)) == 52
    s = dob.issue(KEY, "leap", b, [dt.date(2023, 3, 1), dt.date(2023, 2, 28)], min_window_days=1)
    assert s is None or (all(dob.age(s, r) == dob.age(b, r) for r in (dt.date(2023, 3, 1), dt.date(2023, 2, 28))) and s != b)
    assert all(not (d.month == 2 and d.day == 29) for d in dob.feasible(dt.date(1976, 5, 5), []))


def test_tight_window_falls_back_and_conflict_is_detected():
    b = dt.date(1980, 6, 15)
    refs = [dt.date(2020, 6, 14), dt.date(2021, 6, 16)]      # window of a few days only
    assert dob.issue(KEY, "t", b, refs, min_window_days=14) is None
    s = dob.issue(KEY, "t2", b, [dt.date(2020, 1, 1)], min_window_days=14)
    assert s is not None
    later = [dt.date(2020, 1, 1), dt.date(2022, (s.month if s.month != b.month else 12), 1)]
    if any(dob.age(s, r) != dob.age(b, r) for r in later):
        assert dob.conflicts(b, s, later)


def test_dob_rendering_keeps_each_format():
    d = dt.date(1968, 12, 8)
    s = dt.date(1968, 11, 3)
    for text, want in (("08 December 1968", "03 November 1968"), ("8/12/1968", "3/11/1968"), ("08.12.1968", "03.11.1968"),
                       ("08/12/68", "03/11/68"), ("8th December 1968", "3rd November 1968"), ("December 8, 1968", "November 3, 1968"),
                       ("December 1968", "November 1968")):
        f = dob.parse(text)
        assert f is not None and f.date.year == 1968, text
        assert dob.render(f, s) == want, text
    assert dob.parse("1968").kind == "year"


# ---------------------------------------------------------------- identifiers (§4.9.3)
KINDS = {"MEDICARE": ("2123 45670 1", "medicare"), "ABN": ("51 824 753 556", "abn"), "ACN": ("004 085 616", "acn"),
         "PROVIDER_NUMBER": ("2077 09JB", "provider_number"), "IHI": ("8003 6012 3456 7891", "ihi")}


@settings(max_examples=60, deadline=None)
@given(st.sampled_from(sorted(KINDS)), st.integers(min_value=0, max_value=10_000))
def test_checksummed_surrogates_never_validate(kind, salt):
    value, validator = KINDS[kind]
    key = KEY + salt.to_bytes(4, "big")
    sur = identifiers.surrogate(key, kind, value, taken=set(), forbidden_digits={v.digits_only(value)})
    assert not v.validate(validator, sur)
    assert sur != value
    if kind == "PROVIDER_NUMBER":   # six digits, a location character (digit or letter) and a check letter
        assert sum(c.isalnum() for c in sur) == 8 and sur.replace(" ", "")[:6].isdigit()
    else:
        assert [c.isdigit() for c in sur] == [c.isdigit() for c in value]


def test_format_preserving_and_collision_checks():
    s = identifiers.surrogate(KEY, "CLAIM_NUMBER", "WC1234567", taken=set(), forbidden_digits=set())
    assert len(s) == 9 and s[:2].isalpha() and s[:2].isupper() and s[2:].isdigit() and s != "WC1234567"
    forb = {v.digits_only(s)}
    s2 = identifiers.surrogate(KEY, "CLAIM_NUMBER", "WC1234567", taken=set(), forbidden_digits=forb)
    assert v.digits_only(s2) not in forb


def test_phones_fall_in_reserved_ranges():
    pools = load_pools()
    for value in ("0412 345 678", "(07) 5575 2444", "+61 412 345 678", "1300 362 128", "1800 123 456", "5575 2444"):
        out = contact.phone(KEY, value, pools, set())
        assert contact.is_reserved_phone(out, pools), value
        assert len(v.digits_only(out)) == len(v.digits_only(value)) or value.startswith("13")


# ---------------------------------------------------------------- pipeline: names, TFN, NATIONALITY
LINES = [
    "Re: John Smith",
    "Dear Mr Smith, SMITH, John and J. Smith attended. John was seen on 3 March 2024.",
    "Mr Smith is a 45-year-old male, date of birth 14/02/1979. He was born in Vietnam and is an Indian national.",
    "His TFN is 123 456 782 and his Medicare number is 2123 45670 1.",
    "He wrote to the Australian Taxation Office about his claim.",
    "Contact 0491 570 156 or john.smith@corp.com.au.",
    "Date of injury: 20/06/2023.",
]


@pytest.fixture(scope="module")
def result():
    doc = DocumentText("doc_901", {1: page_from_lines("doc_901", 1, LINES)}, {})
    spans = run_detector(create("baseline"), [doc])
    return doc, spans, pseudonymize([doc], spans, policy=POLICY, taxonomy=TAX, vault=Vault.ephemeral())


def test_names_are_consistent_across_forms(result):
    doc, _, r = result
    edits = [e for e in r.documents["doc_901"].edits if e.entity_type == "PERSON"]
    surs = {json.loads(e.identity).get("surname") for e in edits if "surname" in json.loads(e.identity)}
    givs = {json.loads(e.identity).get("given") for e in edits if "given" in json.loads(e.identity)}
    assert len(surs) == 1 and len(givs) == 1
    out = r.documents["doc_901"].pages[1]
    assert "Smith" not in out and "SMITH" not in out and "John" not in out
    sur = next(iter(surs))
    assert sur.upper() in out                                  # "SMITH, John" keeps capitals and order
    pools = load_pools()
    assert next(iter(givs)) in {x.casefold() for x in pools.male}   # Mr -> male surrogate first name
    assert "Mr " in out                                        # titles are never changed


def test_tfn_nationality_and_ato(result):
    _, _, r = result
    out = r.documents["doc_901"].pages[1]
    assert "[TFN]" in out and "123 456 782" not in out
    assert out.count("[NATIONALITY]") >= 2 and "Vietnam" not in out and "Indian" not in out
    assert "Australian Taxation Office" in out


def test_identifier_and_contact_surrogates(result):
    _, _, r = result
    out = r.documents["doc_901"].pages[1]
    meds = [e for e in r.documents["doc_901"].edits if e.entity_type == "MEDICARE"]
    assert meds and not v.validate("medicare", meds[0].replacement)
    emails = [e for e in r.documents["doc_901"].edits if e.entity_type == "EMAIL"]
    assert emails and emails[0].replacement.split("@")[1] in ("example.com", "example.org", "example.net")
    assert "corp.com.au" not in out and "0491 570 156" not in out or "0491 570 156" in load_pools().mobile


def test_dob_in_pipeline_keeps_age(result):
    _, _, r = result
    d = r.dob_issued
    assert d and all(x["age_preserved"] and x["year_kept"] and x["changed"] for x in d if x["window_ok"])
    out = r.documents["doc_901"].pages[1]
    assert "14/02/1979" not in out and "/1979" in out


def test_replacement_is_deterministic_and_surrogates_avoid_source(result):
    doc, spans, r = result
    r2 = pseudonymize([doc], spans, policy=POLICY, taxonomy=TAX, vault=Vault.ephemeral())
    assert output_digest(r) == output_digest(r2)
    src = doc.pages[1].text.casefold()
    for e in r.documents["doc_901"].edits:
        for comp in json.loads(e.identity or "{}").values():
            assert comp not in src


def test_name_parse_forms():
    assert [t.role for t in parse("SMITH, John")] == ["surname", "given"]
    assert [t.role for t in parse("KNIGHT Julie Michelle")] == ["surname", "given", "given"]
    assert [t.role for t in parse("J. Smith")] == ["initial", "surname"]
    assert [t.role for t in parse("JK")] == ["initial"]
    assert parse("Smith", honorific="Mr")[0].role == "surname"


# ---------------------------------------------------------------- vault (§4.9.5)
def test_vault_encryption_frozen_mappings_and_one_way_redaction(tmp_path):
    p = tmp_path / "m.sqlite3"
    vlt = Vault.open("m", passphrase="correct horse", path=p)
    vlt.put("surname", "Quillfeather", "Abernethy")
    vlt.add_redaction("TFN", "123 456 782")
    with pytest.raises(VaultError):
        vlt.put("surname", "Quillfeather", "Bancroft")
    assert vlt.reverse("surname", "Abernethy") == ["Quillfeather"]
    vlt.close()
    raw = p.read_bytes()
    assert b"Quillfeather" not in raw and b"123 456 782" not in raw and b"123456782" not in raw
    with pytest.raises(VaultError):
        Vault.open("m", passphrase="wrong", path=p)
    again = Vault.open("m", passphrase="correct horse", path=p)
    assert again.get("surname", "quillfeather") == "Abernethy" and again.is_redacted_value("TFN", "123 456 782")
    assert again.bump_epoch() == 2
    tables = {r[0] for r in sqlite3.connect(str(p)).execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"mapping", "redaction", "dob", "review"} <= tables


def test_vault_passphrase_from_the_os_credential_store(data_root, monkeypatch):
    import pytest
    from redactor.replacement.vault import Vault, VaultError
    from redactor.security import credstore
    if not credstore.supported():
        pytest.skip("OS credential store supported on Windows only")
    monkeypatch.delenv("REDACTOR_VAULT_PASSPHRASE", raising=False)
    name = credstore.target("matter_selftest_synthetic")
    credstore.delete(name)
    with pytest.raises(VaultError):
        Vault.open("matter_selftest_synthetic")
    try:
        credstore.store(name, "synthetic-test-passphrase-123")
        assert credstore.load(name) == "synthetic-test-passphrase-123"
        v = Vault.open("matter_selftest_synthetic")
        v.put("surname", "quillfeather", "Hartigan")
        v.commit()
        v.close()
        assert Vault.open("matter_selftest_synthetic").get("surname", "quillfeather") == "Hartigan"
    finally:
        assert credstore.delete(name) and credstore.load(name) is None
