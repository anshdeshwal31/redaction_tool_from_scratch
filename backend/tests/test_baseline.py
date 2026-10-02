"""Baseline detector v0/v1 on synthetic documents (plan §7, §12). Every name and number is invented."""

from __future__ import annotations

from redactor.core.types import BBox, DocumentText, PageExtraction, RawWord, build_page_text
from redactor.detectors.base import create, run_detector


def page_from_lines(doc_id: str, page: int, lines: list[str], *, x_step: float = 7.0) -> PageExtraction:
    raw = []
    for li, line in enumerate(lines):
        x = 50.0
        for word in line.split(" "):
            if word:
                raw.append(RawWord(word, BBox(x, 80 + 18 * li, x + x_step * len(word), 92 + 18 * li), 96.0, 1, li))
                x += x_step * len(word) + 6
    text, words = build_page_text(raw)
    return PageExtraction(doc_id, page, "synthetic#1", "ocr", 595.0, 842.0, words, text, {})


LINES = [
    "Re: Alexandra Quill",
    "Date of Birth: 14/02/1979",
    "Dear Mr Bramble,",
    "I examined Ms Quill on 3 March 2024 at 12 Example Street Sampleton QLD 4999.",
    "Ms Quill is a 45-year-old female. She was born in Vietnam and is an Australian citizen.",
    "Her Medicare number is 2123 45670 1 and her TFN is 123 456 782.",
    "Unrelated nine digits 123 456 782 appear here without a label.",
    "Contact 0491 570 156 or quill.sample@example.com. The clinic line is 1300 555 010.",
    "Date of injury: 20/06/2023. Claim number: WC1234567.",
    "She wrote to the Australian Taxation Office and WorkCover Queensland.",
    "Referred by Sampleton Medical Centre and Quill Lawyers.",
    "Alexandra later called PO Box 123 Sampleton QLD 4999 about ABN 51 824 753 556.",
    "Yours faithfully,",
    "Dr Harriet Bramble",
]


def _doc():
    return DocumentText("doc_900", {1: page_from_lines("doc_900", 1, LINES)}, {"filename": "Report of Alexandra Quill.pdf"})


def _found(spans, text):
    return {(s.entity_type, s.text) for s in spans}


def test_baseline_v0_rule_families():
    det = create("baseline")
    spans = run_detector(det, [_doc()])["doc_900"]
    found = _found(spans, None)
    expect = {
        ("PERSON", "Alexandra Quill"), ("DATE_OF_BIRTH", "14/02/1979"), ("PERSON", "Bramble"),
        ("DATE", "3 March 2024"), ("ADDRESS", "12 Example Street Sampleton QLD 4999"), ("AGE", "45-year-old"),
        ("GENDER", "female"), ("NATIONALITY", "Vietnam"), ("NATIONALITY", "Australian"),
        ("MEDICARE", "2123 45670 1"), ("TFN", "123 456 782"), ("PHONE", "0491 570 156"),
        ("EMAIL", "quill.sample@example.com"), ("PHONE", "1300 555 010"), ("DATE", "20/06/2023"),
        ("CLAIM_NUMBER", "WC1234567"), ("ORGANIZATION", "Australian Taxation Office"),
        ("ORGANIZATION", "WorkCover Queensland"), ("ORGANIZATION", "Sampleton Medical Centre"),
        ("ORGANIZATION", "Quill Lawyers"), ("PERSON", "Alexandra"),
        ("ADDRESS", "PO Box 123 Sampleton QLD 4999"), ("ABN", "51 824 753 556"), ("PERSON", "Harriet Bramble"),
        ("PERSON", "Alexandra Quill"),
    }
    missing = expect - found
    assert not missing, missing
    # the unlabelled checksum-valid nine digits are not a TFN (context required)
    tfns = [s for s in spans if s.entity_type == "TFN"]
    assert len(tfns) == 1
    # "Australian" inside "Australian Taxation Office" is not a nationality
    ato = next(s for s in spans if s.text == "Australian Taxation Office")
    assert not any(s.entity_type == "NATIONALITY" and ato.start <= s.start < ato.end for s in spans)
    attrs = {s.text: dict(s.attributes) for s in spans}
    assert attrs["20/06/2023"]["date_role"] == "date_of_injury"
    assert attrs["3 March 2024"]["date_role"] == "examination"
    assert attrs["1300 555 010"]["number_class"] == "1300"
    assert attrs["WorkCover Queensland"]["role"] == "statutory_body"
    assert attrs["Quill Lawyers"]["role"] == "law_firm"
    # filename field is scanned too
    assert any(s.field == "filename" and s.entity_type == "PERSON" for s in spans)
    # no overlapping spans on one text source
    page = sorted((s for s in spans if s.page == 1), key=lambda s: s.start)
    assert all(a.end <= b.start for a, b in zip(page, page[1:]))


def test_baseline_is_deterministic():
    a = run_detector(create("baseline"), [_doc()])["doc_900"]
    b = run_detector(create("baseline"), [_doc()])["doc_900"]
    assert [s.to_dict() for s in a] == [s.to_dict() for s in b]
    assert create("baseline").fingerprint() == create("baseline").fingerprint()


def _form_page():
    """A two-column form whose OCR reading order lists every label first, then every value."""
    raw = []
    labels = ["Surname:", "Given names:", "Date of birth:", "Telephone:"]
    values = ["QUILLSTONE", "Maribel Anne", "07/11/1985", "0491 570 157"]
    for i, lab in enumerate(labels):
        x = 50.0
        for w in lab.split(" "):
            raw.append(RawWord(w, BBox(x, 100 + 30 * i, x + 7 * len(w), 112 + 30 * i), 90.0, 1, i))
            x += 7 * len(w) + 5
    for i, val in enumerate(values):
        x = 260.0
        for w in val.split(" "):
            raw.append(RawWord(w, BBox(x, 100 + 30 * i, x + 7 * len(w), 112 + 30 * i), 90.0, 2, i))
            x += 7 * len(w) + 5
    text, words = build_page_text(raw)
    return PageExtraction("doc_901", 1, "form#1", "ocr", 595.0, 842.0, words, text, {})


def test_v1_layout_rules_recover_split_label_value_pairs():
    doc = DocumentText("doc_901", {1: _form_page()})
    v0 = {(s.entity_type, s.text) for s in run_detector(create("baseline"), [doc])["doc_901"]}
    v1 = {(s.entity_type, s.text) for s in run_detector(create("baseline_v1"), [doc])["doc_901"]}
    assert ("PERSON", "QUILLSTONE") not in v0 and ("PERSON", "Maribel Anne") not in v0
    assert {("PERSON", "QUILLSTONE"), ("PERSON", "Maribel Anne"), ("DATE_OF_BIRTH", "07/11/1985")} <= v1
    spans = run_detector(create("baseline_v1"), [doc])["doc_901"]
    assert any(s.rule_id and s.rule_id.startswith("kv:au_generic:") for s in spans)
