"""Silver-pass converter on synthetic text: locating surfaces, regions, deterministic IDs (plan §12 'Silver files')."""

from __future__ import annotations

import pytest

from redactor.core.types import BBox, PageExtraction, RawWord, build_page_text
from redactor.dataset import silver


def _page(lines):
    raw = []
    for li, line in enumerate(lines):
        x = 50.0
        for word in line.split(" "):
            raw.append(RawWord(word, BBox(x, 100 + 20 * li, x + 6 * len(word), 110 + 20 * li), 95.0, 1, li))
            x += 6 * len(word) + 4
    text, words = build_page_text(raw)
    return PageExtraction("doc_900", 1, "src#1", "ocr", 595.0, 842.0, words, text, {})


def test_locate_line_occurrence_and_boundaries():
    pe = _page(["Mr Alex Sample and Alex Samples", "Alex Sample again"])
    assert silver.locate(pe.text, "Alex Sample", 1) == [(3, 14)]
    with pytest.raises(silver.SilverError):
        silver.locate(pe.text, "Alex Sample", 1, 2)  # "Alex Samples" is not a word-boundary match
    assert silver.locate(pe.text, "Alex Sample", 2) == [(32, 43)]
    assert len(silver.locate(pe.text, "Alex Sample", None, all_=True)) == 2
    assert silver.locate(pe.text, "Samples\nAlex", 1) == [(24, 36)]


def test_regions_one_per_line_and_partial_words():
    pe = _page(["12 Example Street", "SAMPLEVILLE QLD 4000"])
    s, e = silver.locate(pe.text, "12 Example Street\nSAMPLEVILLE QLD 4000", 1)[0]
    regions = silver.regions_for_span(pe, s, e)
    assert len(regions) == 2 and regions[0].y0 < regions[1].y0
    s, e = silver.locate(pe.text, "Example", 1)[0]
    (box,) = silver.regions_for_span(pe, s, e)
    word = next(w for w in pe.words if w.text == "Example")
    assert box == BBox(word.bbox.x0, word.bbox.y0, word.bbox.x1, word.bbox.y1)


def test_build_entities_is_deterministic(data_root, monkeypatch):
    pe = _page(["Mr Alex Sample was seen on 3 March 2024", "Phone 0491 570 156 Medicare 2123 45670 1"])
    monkeypatch.setattr(silver.cache, "load_reference_pages", lambda doc: {1: pe})
    monkeypatch.setattr(silver.cache, "load_fields", lambda doc: {"filename": "Report of Alex Sample.pdf"})
    proposals = [
        {"p": 1, "t": "MEDICARE", "l": 2, "s": "2123 45670 1"},
        {"p": 1, "t": "PERSON", "l": 1, "s": "Alex Sample", "role": "plaintiff", "cid": "person_001",
         "a": {"name_form": "full"}},
        {"p": 1, "t": "DATE", "l": 1, "s": "3 March 2024", "a": {"date_role": "examination"}},
        {"p": 1, "t": "PHONE", "l": 2, "s": "0491 570 156", "gold": "0491 570 157", "a": {"number_class": "mobile"}},
        {"field": "filename", "t": "PERSON", "s": "Alex Sample", "role": "plaintiff", "cid": "person_001"},
        {"p": 1, "t": "SIGNATURE", "box": [0.1, 0.8, 0.3, 0.85], "cid": "person_001"},
    ]
    ents1, _ = silver.build_entities("doc_900", proposals)
    ents2, _ = silver.build_entities("doc_900", list(reversed(proposals)))
    assert [e.dump() for e in ents1] == [e.dump() for e in ents2]
    by_type = {e.entity_type: e for e in ents1 if e.page is not None}
    assert by_type["MEDICARE"].attributes["checksum"] == "valid" and by_type["MEDICARE"].action == "SYNTHETIC"
    assert by_type["DATE"].action == "KEEP"
    assert "ocr_degraded" in by_type["PHONE"].flags and by_type["PHONE"].text == "0491 570 157"
    assert by_type["SIGNATURE"].text is None and by_type["SIGNATURE"].action == "REDACT"
    field_ent = next(e for e in ents1 if e.field == "filename")
    assert field_ent.page is None and field_ent.text_anchor.text_source_id == "field:filename"
    assert all(e.provenance.origin == "silver" for e in ents1)
    assert [e.entity_id for e in ents1] == [f"doc_900.e{n:04d}" for n in range(1, len(ents1) + 1)]
