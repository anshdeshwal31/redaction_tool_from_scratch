"""Release gate on synthetic pages (plan §12 "Gate"). All names and numbers are invented."""

from __future__ import annotations

import inspect

import yaml

from redactor.core.types import BBox, DocumentText, PageExtraction, RawWord, build_page_text
from redactor.gate import queue
from redactor.gate.gate import run_gate
from redactor.paths import config_dir
from redactor.replacement.vault import Vault
from redactor.taxonomy import load_policy, load_taxonomy

POLICY, TAX = load_policy(), load_taxonomy()
CFG = yaml.safe_load((config_dir() / "release_gate.v0.1.yaml").read_text(encoding="utf-8"))
DET = {"detectors": [{"name": "baseline"}]}


def pe(doc, page, lines, conf, source="ocr.test#1", method="ocr", shift=0.0):
    raw = []
    for li, line in enumerate(lines):
        x = 50.0
        for w in line.split(" "):
            raw.append(RawWord(w, BBox(x + shift, 80 + 18 * li, x + shift + 7 * len(w), 92 + 18 * li), conf, 1, li))
            x += 7 * len(w) + 6
    text, words = build_page_text(raw)
    return PageExtraction(doc, page, source, method, 595.0, 842.0, words, text, {})


CLEAN = ["Re: Ms Alexandra Quillfeather", "Ms Quillfeather attended the clinic for review on 3 March 2024."]


def run(pages, info, *, attempt_fn=None, decisions=(), handwriting=None):
    docs = {"doc_1": DocumentText("doc_1", pages, {})}
    kw = {"attempt_fn": attempt_fn} if attempt_fn else {"attempt_fn": lambda *a: []}
    return run_gate(docs, {"doc_1": info}, detector_cfg=DET, policy=POLICY, taxonomy=TAX, gate_cfg=CFG, vault=Vault.ephemeral(),
                    decisions=decisions, handwriting=handwriting, **kw)


def test_clean_page_passes_and_exports():
    r = run({1: pe("doc_1", 1, CLEAN, 96.0)}, {1: {"class": "scanned"}})
    assert [p.state for p in r.pages] == ["PASS"] and r.export_allowed["doc_1"]
    assert "Quillfeather" not in r.matter.documents["doc_1"].pages[1]


def test_unknown_and_handwriting_go_straight_to_review_without_retry():
    calls = []
    fn = lambda d, p, rung, ref: calls.append(rung["name"]) or []
    r = run({1: pe("doc_1", 1, CLEAN, 30.0), 2: pe("doc_1", 2, CLEAN, 30.0)}, {1: {"class": "unknown"}, 2: {"class": "scanned"}},
            attempt_fn=fn, handwriting={"doc_1": {2}})
    assert all(p.state == "REVIEW" for p in r.pages)
    assert "unknown_page_class" in r.pages[0].reasons and "handwriting" in r.pages[1].reasons
    assert calls == [] and not r.export_allowed["doc_1"]


def test_low_confidence_retry_rescues_and_never_waives_checks():
    good = pe("doc_1", 1, CLEAN, 95.0, source="ocr.test#dpi400")
    fn = lambda d, p, rung, ref: [good] if rung["name"] == "dpi400" else []
    r = run({1: pe("doc_1", 1, CLEAN, 40.0)}, {1: {"class": "scanned"}}, attempt_fn=fn)
    assert r.pages[0].low_confidence and r.pages[0].rescued_by == "dpi400" and r.pages[0].state == "PASS"
    still_bad = pe("doc_1", 1, CLEAN, 50.0, source="ocr.test#retry")
    r2 = run({1: pe("doc_1", 1, CLEAN, 40.0)}, {1: {"class": "scanned"}}, attempt_fn=lambda *a: [still_bad])
    assert r2.pages[0].state == "REVIEW" and "low_confidence" in r2.pages[0].reasons
    assert all(a["low"] for a in r2.pages[0].attempts)          # every attempt re-ran the same checks
    assert not r2.export_allowed["doc_1"]


def test_union_of_attempts_never_drops_a_protection():
    ref = pe("doc_1", 1, CLEAN + ["Contact 0491 570 156 today."], 40.0)
    # the rescued attempt lost the phone line entirely
    rescued = pe("doc_1", 1, CLEAN, 95.0, source="ocr.test#retry")
    r = run({1: ref}, {1: {"class": "scanned"}}, attempt_fn=lambda *a: [rescued])
    assert r.pages[0].rescued_by and any(i["reason"] == "unprojectable_detection" for i in r.items)
    assert r.regions and r.pages[0].state == "REVIEW"


def test_planted_residual_blocks_export_and_decisions_are_inputs():
    lines = CLEAN + ["The file was later sent to Qu1llfeather at home.", "Unlabelled 123 456 782 appears here."]
    r = run({1: pe("doc_1", 1, lines, 96.0)}, {1: {"class": "scanned"}})
    methods = {h.method for h in r.hits}
    assert "vault_fuzzy" in methods and "checksum_valid" in methods
    assert r.pages[0].state == "BLOCKED" and not r.export_allowed["doc_1"]
    leak_items = [i for i in r.items if i["reason"].startswith("leak_")]
    decs = [{"item_id": i["item_id"], "decision": "not_pii", "reason": "false_alarm", "payload": {}, "reviewer": "AB"} for i in leak_items]
    r2 = run({1: pe("doc_1", 1, lines, 96.0)}, {1: {"class": "scanned"}}, decisions=decs)
    assert r2.pages[0].state in ("REVIEW_DECIDED", "REVIEW") and all(i["resolved"] for i in r2.items if i["reason"].startswith("leak_"))
    r3 = run({1: pe("doc_1", 1, lines, 96.0)}, {1: {"class": "scanned"}}, decisions=decs)
    assert [p.state for p in r2.pages] == [p.state for p in r3.pages]


def test_no_force_path_and_decision_rules():
    assert "force" not in inspect.signature(run_gate).parameters
    item = {"item_id": "x", "reason": "handwriting"}
    import pytest
    with pytest.raises(queue.QueueError):
        queue.validate_decision(item, "accept_ocr", None, None)        # only for confidence triggers
    with pytest.raises(queue.QueueError):
        queue.validate_decision(item, "not_pii", "because", None)
    assert not queue.resolves({"reason": "handwriting"}, {"decision": "flag_handwriting"})
    assert queue.resolves({"reason": "low_confidence"}, {"decision": "accept_ocr"})
