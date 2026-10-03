"""Combiner strategies and C2 options (plan §4.4, §11 C2) with synthetic dummy detectors."""

from __future__ import annotations

import pytest

from redactor.core.types import DocumentText, EntitySpan, PageExtraction, RawWord, BBox, build_page_text
from redactor.detectors.combiner import Combined

TEXT = "Ms Wren Ashdale works at Brookvale Clinic in Sampleton"


def _doc():
    raw, x = [], 0.0
    for w in TEXT.split(" "):
        raw.append(RawWord(w, BBox(x, 0, x + 5, 10), 99.0, 1, 0))
        x += 6
    t, words = build_page_text(raw)
    return DocumentText("d", {1: PageExtraction("d", 1, "src", "text_layer", 100.0, 100.0, words, t, {})}, {})


class Dummy:
    version = "0.1.0"

    def __init__(self, name, finds):
        self.name, self.finds = name, finds

    def fingerprint(self):
        return f"{self.name}#x"

    def detect(self, doc):
        out = []
        for surface, typ, conf in self.finds:
            i = TEXT.index(surface)
            out.append(EntitySpan("d", 1, None, "src", i, i + len(surface), surface, typ, typ, conf, f"{self.name}@0.1.0", "r"))
        return out


BASE = Dummy("baseline", [("Ashdale", "PERSON", 1.0), ("Brookvale Clinic", "ORGANIZATION", 1.0)])
NER = Dummy("presidio_ner", [("Wren Ashdale", "PERSON", 0.85), ("Brookvale", "LOCATION", 0.4), ("Sampleton", "LOCATION", 0.9)])


def spans(c):
    return [(s.text, s.entity_type, s.detector.split("@")[0]) for s in c.detect(_doc())]


def test_strategies():
    assert len(spans(Combined([BASE, NER], "union"))) == 5
    # detector order wins overlaps by default
    assert spans(Combined([BASE, NER], "union_resolved")) == [
        ("Ashdale", "PERSON", "baseline"), ("Brookvale Clinic", "ORGANIZATION", "baseline"), ("Sampleton", "LOCATION", "presidio_ner")]
    assert [t for t, _, _ in spans(Combined([BASE, NER], "intersection"))] == ["Ashdale"]
    assert [t for t, _, _ in spans(Combined([BASE, NER], "majority"))] == ["Ashdale"]


def test_c2_options_only_types_type_priority_min_confidence():
    c = Combined([BASE, NER], "union_resolved", ("type_priority", "priority", "longest", "earliest", "type_name"),
                 only_types={"presidio_ner": ["PERSON", "LOCATION"]}, type_priority={"PERSON": ["presidio_ner", "baseline"]},
                 min_confidence={"presidio_ner": 0.5})
    assert spans(c) == [("Wren Ashdale", "PERSON", "presidio_ner"), ("Brookvale Clinic", "ORGANIZATION", "baseline"),
                        ("Sampleton", "LOCATION", "presidio_ner")]
    plain = Combined([BASE, NER])
    assert c.fingerprint() != plain.fingerprint()
    # deterministic regardless of detector output order
    rev = Combined([BASE, Dummy("presidio_ner", list(reversed(NER.finds)))], "union_resolved",
                   ("type_priority", "priority", "longest", "earliest", "type_name"),
                   only_types={"presidio_ner": ["PERSON", "LOCATION"]}, type_priority={"PERSON": ["presidio_ner", "baseline"]},
                   min_confidence={"presidio_ner": 0.5})
    assert spans(rev) == spans(c)
    with pytest.raises(ValueError):
        Combined([BASE], "union_resolved", ("nonsense",))
