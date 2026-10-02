"""F5 evaluation core on synthetic data with hand-computed expectations (plan §12). All names invented."""

from __future__ import annotations

import itertools
from dataclasses import replace

import pytest

from redactor.core.types import BBox, DocumentText, EntitySpan, PageExtraction, RawWord, build_page_text
from redactor.dataset.models import AnnotationSet, Coverage, Entity, Provenance, Region, TextAnchor
from redactor.evaluation import determinism as det
from redactor.evaluation.evaluate import evaluate_split
from redactor.evaluation.gold import GoldDocument, project_document, project_regions
from redactor.evaluation.iaa import b_cubed, cohen_kappa, compare
from redactor.evaluation.matching import MSpan, covered_chars, match
from redactor.evaluation.metrics import prf
from redactor.evaluation.ocr_metrics import bag_of_words_f1, cer, surface_recovery, wer
from redactor.reporting import leak
from redactor.taxonomy import load_policy, load_taxonomy

SRC = "synthetic#1"
LINES = ["Ms Wren Ashdale attended on 3 March 2024.", "Contact Tamsin Hollow at Brookvale Clinic."]
POLICY = load_policy()
TAX = load_taxonomy()


def page(lines=LINES) -> PageExtraction:
    raw = []
    for li, line in enumerate(lines):
        x = 50.0
        for word in line.split(" "):
            raw.append(RawWord(word, BBox(x, 80 + 18 * li, x + 7 * len(word), 92 + 18 * li), 95.0, 1, li))
            x += 7 * len(word) + 6
    text, words = build_page_text(raw)
    return PageExtraction("doc_t", 1, SRC, "ocr", 595.0, 842.0, words, text, {})


PE = page()
DOC = DocumentText("doc_t", {1: PE}, {})
# (surface, type, role, attributes)
GOLD = [("Wren Ashdale", "PERSON", "plaintiff", {}), ("3 March 2024", "DATE", None, {"date_role": "examination"}),
        ("Tamsin Hollow", "PERSON", "witness", {}), ("Brookvale Clinic", "ORGANIZATION", "medical_practice", {})]


def _entity(i, surface, etype, role, attrs, *, certainty="certain", anchor=True, regions=None, cid=None):
    s = PE.text.index(surface)
    e = s + len(surface)
    words = PE.words_in_span(s, e)
    box = BBox.union_all(w.bbox for w in words)
    dec = POLICY.decide(etype, role, attrs, TAX.validator(etype))
    return Entity(entity_id=f"doc_t.e{i:04d}", canonical_id=cid or (f"matter_t/person_{i:03d}" if etype == "PERSON" else None),
                  entity_type=etype, text=surface, page=1,
                  regions=regions if regions is not None else [Region(x0=box.x0, y0=box.y0, x1=box.x1, y1=box.y1)],
                  text_anchor=TextAnchor(text_source_id=SRC, start=s, end=e) if anchor else None,
                  role=role, attributes=attrs, action=dec.action, certainty=certainty,
                  provenance=Provenance(annotator="A1", pass_=1, origin="manual"))


def gold_doc(entities, annotator="A1") -> GoldDocument:
    ann = AnnotationSet(document_id="doc_t", document_sha256="0" * 64, annotator=annotator, pass_=1, mode="blind", revision=1,
                        guidelines_version="0.1.0", policy_ref=POLICY.ref,
                        coverage=Coverage(pages_complete=[1], types_complete=[]), entities=entities)
    return GoldDocument("doc_t", ann, "verified", "<memory>")


GD = gold_doc([_entity(i, *g) for i, g in enumerate(GOLD, 1)])


def span(surface, etype, *, shift_end=0, n=1):
    s = [i for i in range(len(PE.text)) if PE.text.startswith(surface, i)][n - 1]
    e = s + len(surface) + shift_end
    return EntitySpan("doc_t", 1, None, SRC, s, e, PE.text[s:e], etype, etype, None, "dummy@0", "r")


def run(preds):
    pub, priv, _ = evaluate_split([DOC], {"doc_t": GD}, {"doc_t": preds}, page_classes={"doc_t": {1: "scanned"}},
                                  policy=POLICY, taxonomy=TAX, split="dev", bootstrap_rounds=50)
    return pub


def micro(pub, scheme):
    return pub["detection"][scheme]["micro"]


def test_prf_hand_values():
    assert prf(3, 1, 2) == {"tp": 3, "fp": 1, "fn": 2, "precision": 0.75, "recall": 0.6, "f1": 0.6667}
    assert prf(0, 0, 4)["precision"] is None and prf(0, 0, 4)["recall"] == 0.0


def test_perfect_detector():
    pub = run([span(s, t) for s, t, _, _ in GOLD])
    for scheme in ("strict", "exact_boundary", "overlap_typed", "overlap_any"):
        assert micro(pub, scheme) == {"tp": 4, "fp": 0, "fn": 0, "precision": 1.0, "recall": 1.0, "f1": 1.0}
    # A predicted ORGANIZATION has no role, so the policy resolves it to REVIEW: pessimistic counts it as
    # unprotected (POLICY_MISS), with_review as protected.
    assert pub["protection"]["pessimistic"]["mention_recall"] == 0.6667
    assert pub["protection"]["with_review"]["mention_recall"] == 1.0
    assert pub["protection"]["pessimistic"]["keep_violations"] == 0
    assert pub["attribution"]["all"]["OK"] == 2 and pub["attribution"]["all"]["POLICY_MISS"] == 1


def test_empty_detector():
    pub = run([])
    assert micro(pub, "strict") == {"tp": 0, "fp": 0, "fn": 4, "precision": None, "recall": 0.0, "f1": None}
    assert pub["protection"]["pessimistic"]["residual_mentions"] == 3
    assert pub["attribution"]["all"]["DETECTOR_MISS"] == 3


def test_off_by_one_detector():
    pub = run([span(s, t, shift_end=-1) for s, t, _, _ in GOLD])
    assert micro(pub, "strict")["tp"] == 0 and micro(pub, "strict")["fp"] == 4 and micro(pub, "strict")["fn"] == 4
    assert micro(pub, "exact_boundary")["tp"] == 0
    assert micro(pub, "overlap_any") == {"tp": 4, "fp": 0, "fn": 0, "precision": 1.0, "recall": 1.0, "f1": 1.0}
    assert pub["protection"]["pessimistic"]["mention_recall"] == 0.0   # never fully covered
    assert pub["protection"]["pessimistic"]["char_recall"] < 1.0


def test_type_swapped_detector():
    swap = {"PERSON": "ORGANIZATION", "ORGANIZATION": "PERSON", "DATE": "AGE"}
    pub = run([span(s, swap[t]) for s, t, _, _ in GOLD])
    assert micro(pub, "strict")["tp"] == 0
    assert micro(pub, "exact_boundary")["tp"] == 4
    assert micro(pub, "overlap_typed")["tp"] == 0
    assert micro(pub, "overlap_any")["tp"] == 4
    assert pub["detection"]["type_accuracy"]["exact"] == 0.0
    assert pub["detection"]["confusion"]["PERSON"]["ORGANIZATION"] == 2


def test_keep_violation_and_neutral_predictions():
    # a PERSON prediction over the DATE (KEEP) is a KEEP violation; DATE gold made uncertain -> neutral
    pub = run([span("3 March 2024", "PERSON")])
    assert pub["protection"]["pessimistic"]["keep_violations"] == 1
    ents = [_entity(i, *g, certainty="uncertain" if g[1] == "DATE" else "certain") for i, g in enumerate(GOLD, 1)]
    out, _, _ = evaluate_split([DOC], {"doc_t": gold_doc(ents)}, {"doc_t": [span("3 March 2024", "PERSON")]},
                               page_classes={"doc_t": {1: "scanned"}}, policy=POLICY, taxonomy=TAX, split="dev", bootstrap_rounds=10)
    assert micro(out, "overlap_any")["fp"] == 0 and out["counts"]["pred_neutral"] == 1
    assert out["gold"]["excluded"] == {"uncertain": 1}


def test_projection_by_regions_and_loss():
    s = PE.text.index("Tamsin Hollow")
    words = PE.words_in_span(s, s + len("Tamsin Hollow"))
    assert project_regions([BBox.union_all(w.bbox for w in words)], PE) == (s, s + len("Tamsin Hollow"))
    assert project_regions([BBox(500, 700, 520, 710)], PE) is None
    ent = _entity(9, "Tamsin Hollow", "PERSON", "witness", {}, anchor=False)
    g = gold_doc([ent])
    m = project_document(g, DOC, policy=POLICY, taxonomy=TAX)[0]
    assert m.projected_by == "regions" and PE.text[m.start:m.end] == "Tamsin Hollow"
    lost = _entity(10, "Tamsin Hollow", "PERSON", "witness", {}, anchor=False, regions=[Region(x0=500, y0=700, x1=520, y1=710)])
    m2 = project_document(gold_doc([lost]), DOC, policy=POLICY, taxonomy=TAX)[0]
    assert not m2.projected
    out = run([])
    assert out["gold"]["not_projected"] == 0


def test_matching_is_order_independent():
    g = [MSpan(("d", 1, "", "s"), 0, 10, "PERSON", "g1"), MSpan(("d", 1, "", "s"), 12, 20, "PERSON", "g2")]
    p = [MSpan(("d", 1, "", "s"), 5, 15, "PERSON", "p1"), MSpan(("d", 1, "", "s"), 0, 9, "PERSON", "p2")]
    a = match(g, p, "overlap_any")
    b = [(i, 1 - j) for i, j in match(g, list(reversed(p)), "overlap_any")]
    assert a == sorted(b) == [(0, 1), (1, 0)]
    assert covered_chars(g[0], p) == 10


def test_bootstrap_is_deterministic():
    a = run([span("Wren Ashdale", "PERSON")])["bootstrap"]
    b = run([span("Wren Ashdale", "PERSON")])["bootstrap"]
    assert a == b


def test_determinism_harness_catches_nondeterministic_detector():
    counter = itertools.count()

    def flaky():
        k = next(counter)
        spans = [span("Wren Ashdale", "PERSON"), span("Tamsin Hollow", "PERSON")]
        if k % 3 == 2:
            spans = list(reversed(spans))
        if k % 4 == 3:
            spans = spans[:1]
        return det.spans_payload({"doc_t": spans})

    runs = det.in_process(flaky, 8)
    ref = runs[0]
    summary = det.summarize(ref[0], runs, [], ref[1])
    assert not summary["all_identical"] and summary["in_process"]["identical"] < 8
    assert summary["first_divergence"] is not None
    stable = det.in_process(lambda: det.spans_payload({"doc_t": [span("Wren Ashdale", "PERSON")]}), 5)
    assert det.summarize(stable[0][0], stable, [], stable[0][1])["all_identical"]


def test_first_divergence_has_no_text():
    a = det.spans_payload({"doc_t": [span("Wren Ashdale", "PERSON")]})
    b = det.spans_payload({"doc_t": [span("Wren Ashdale", "ORGANIZATION")]})
    d = det.first_divergence(a, b)
    assert d["counts"]["changed"] == 1 and "Wren" not in str(d)


def test_leak_scan(tmp_path):
    surfaces = [leak.Surface("Wren Ashdale", "PERSON"), leak.Surface("2123 45670 1", "MEDICARE"),
                leak.Surface("4213", "ADDRESS")]
    needles = leak.build_needles(surfaces)
    (tmp_path / "clean.md").write_text("F1 0.4213 for PERSON; recall 0.5\n", encoding="utf-8")
    s, _ = leak.scan_dir(tmp_path, needles)
    assert s["passed"] and s["hits"] == 0
    (tmp_path / "bad.json").write_text('{"x": "seen Ashdale", "y": "2123-45670-1"}', encoding="utf-8")
    s, detail = leak.scan_dir(tmp_path, needles)
    assert not s["passed"] and s["by_method"] == {"digits": 1, "token": 1}
    assert all("needle" in d for d in detail) and "Ashdale" not in str(s)
    (tmp_path / "bad.json").write_text("contact wren  ashdale today", encoding="utf-8")
    s, _ = leak.scan_dir(tmp_path, needles)
    assert s["by_method"].get("exact") == 1


def test_ocr_metrics():
    assert cer("abcd", "abed") == 0.25
    assert wer("a b c d", "a x c d") == 0.25
    assert cer("Wren", "wren", casefold=True) == 0.0
    assert bag_of_words_f1("a b c", "c b a") == 1.0
    assert surface_recovery("Brookvale Clinic", "Brookvale Clinic") == "exact"
    assert surface_recovery("Brookvale Clinic", "Br00kvale Clinic") == "near"
    assert surface_recovery("Brookvale Clinic", "8r##kv@l3 C|in") == "degraded"
    assert surface_recovery("Brookvale Clinic", None) == "missing"


def test_iaa_known_differences():
    a = gold_doc([_entity(i, *g) for i, g in enumerate(GOLD, 1)], "A1")
    # B: same first two, a boundary error on the third (surname only), misses the fourth
    ents_b = [_entity(1, *GOLD[0]), _entity(2, *GOLD[1]), _entity(3, "Hollow", "PERSON", "witness", {}, cid="matter_t/person_003")]
    b = gold_doc(ents_b, "A2")
    pub, priv = compare(DOC, a, b, policy=POLICY, taxonomy=TAX)
    assert pub["span"]["strict"]["a_as_gold"] == prf(2, 1, 2)          # P 2/3, R 2/4
    assert pub["span"]["strict"]["a_as_gold"]["f1"] == 0.5714
    assert pub["span"]["overlap_any"]["a_as_gold"]["f1"] == 0.8571      # P 3/3, R 3/4
    cats = sorted(d["category"] for d in priv)
    assert cats == ["boundary", "missed"]
    assert pub["region_iou_mean"] is not None and pub["region_iou_mean"] < 1.0


def test_kappa_and_b_cubed_hand_values():
    # po = 0.8, pe = 0.5*0.6 + 0.5*0.4 = 0.5 -> kappa 0.6
    a = ["P"] * 5 + ["N"] * 5
    b = ["P"] * 5 + ["N"] * 3 + ["P"] * 2
    assert cohen_kappa(a, b) == pytest.approx(0.6, abs=1e-4)
    # A clusters {1,2,3}, B clusters {1,2},{3}: P = (1+1+1)/3 = 1, R = (2/3+2/3+1/3)/3 = 5/9
    r = b_cubed([("x", "u"), ("x", "u"), ("x", "v")])
    assert r["precision"] == 1.0 and r["recall"] == pytest.approx(5 / 9, abs=1e-4)
