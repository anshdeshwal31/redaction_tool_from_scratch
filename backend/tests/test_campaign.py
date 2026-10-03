"""A2 tooling on synthetic documents: recall audit, transcripts, migration, freeze and verify (plan §9)."""

from __future__ import annotations

import pytest

from redactor.core.canonical import read_json, write_canonical
from redactor.dataset import campaign, store
from redactor.dataset.models import CanonicalRegistry, RegistryEntity

from test_store_api import entity, repo  # noqa: F401 - fixture reuse


def _gold(doc):
    reg = CanonicalRegistry(matter_id="matter_001", annotator="adjudicated", entities=[
        RegistryEntity(canonical_id="matter_001/person_001", entity_type="PERSON", role="plaintiff", gender="unknown", label="x")])
    write_canonical(store.registry_path("adjudicated"), reg.dump())
    sk = store.skeleton("adjudicated", doc)
    ann = sk.model_copy(update={"entities": [entity(doc, "Wren Ashdale", eid=7)],
                                "coverage": sk.coverage.model_copy(update={"pages_complete": [1]})})
    store.save("adjudicator", "adjudicated", doc, ann, 0)


def test_audit_pool_and_decisions(repo):  # noqa: F811
    _gold("doc_002")
    out = campaign.audit_pool("doc_002")
    assert out["items"] >= 1 and out["pending"] == out["items"]       # the phone/date the gold does not hold yet
    # P7 pools every local deterministic detector plus the scans; one that cannot run is skipped and recorded
    assert {"baseline", "baseline_v1", "audit_scans"} <= set(out["detectors"])
    assert set(out["detectors"]) | set(out["skipped"]) == set(campaign.AUDIT_DETECTORS)
    assert "philter" not in campaign.AUDIT_DETECTORS and "philter_au" not in campaign.AUDIT_DETECTORS
    data = read_json(repo / "golden_dataset" / "audit" / "doc_002.audit.json")
    assert all(not (i["start"] < 0) for i in data["items"])
    with pytest.raises(campaign.CampaignError):
        campaign.audit_decide("doc_002", 0, "reject", reason="because")
    campaign.audit_decide("doc_002", 0, "reject", reason="not_pii")
    again = campaign.audit_pool("doc_002")                              # decisions survive a re-pool
    assert again["pending"] == out["pending"] - 1


def test_transcript_seed_and_verified_only(repo):  # noqa: F811
    out = campaign.seed_transcript("doc_002", 1)
    assert out["lines"] >= 2
    with pytest.raises(campaign.CampaignError):
        campaign.seed_transcript("doc_002", 1)
    assert campaign.load_transcripts("doc_002") == {}                  # not verified yet
    p = repo / "golden_dataset" / "transcripts" / "doc_002" / "p0001.json"
    t = read_json(p)
    t["verified_by"] = "AB"
    write_canonical(p, t)
    assert "Ashdale" in campaign.load_transcripts("doc_002")[1]


def test_migration_keeps_original(repo):  # noqa: F811
    _gold("doc_002")
    p = store.annotation_path("adjudicated", "doc_002")
    r = campaign.migrate_file(p, "0.2.0")
    assert r["status"] == "migrated" and read_json(p)["schema_version"] == "0.2.0"
    assert p.with_name(p.name + ".v0.1.0.orig").exists()
    with pytest.raises(campaign.CampaignError):
        campaign.migrate_file(p, "9.9.9")


def test_freeze_then_verify_detects_changes(repo):  # noqa: F811
    _gold("doc_002")
    out = campaign.freeze("1.0.0")
    assert out["ok"] and out["files"] > 0
    assert campaign.verify()["ok"]
    gold = store.annotation_path("adjudicated", "doc_002")
    assert read_json(gold)["entities"][0]["entity_id"] == "doc_002.e0001"     # renumbered by the fixed sort
    raw = read_json(gold)
    raw["entities"][0]["notes"] = "changed after freeze"
    write_canonical(gold, raw)
    v = campaign.verify()
    assert not v["ok"] and v["changed"] == 1


def test_oracle_source_and_ocr_vs_oracle(repo):  # noqa: F811
    """Plan §4.3: verified transcripts become the gold_transcript source; detection on it is compared with the
    extracted text of the same pages, and attribution learns which misses are the detector's."""
    from redactor.evaluation.gold import load_gold
    from redactor.evaluation.oracle import METHOD, oracle_documents, restrict_gold
    from redactor.experiments import runner
    from redactor.taxonomy import load_policy, load_taxonomy
    _gold("doc_002")
    campaign.seed_transcript("doc_002", 1)
    docs = [runner.load_document_text("doc_002")]
    assert oracle_documents(docs) == {}                                 # unverified transcripts never count
    p = repo / "golden_dataset" / "transcripts" / "doc_002" / "p0001.json"
    t = read_json(p)
    t["verified_by"] = "AB"
    write_canonical(p, t)
    od = oracle_documents(docs)["doc_002"]
    pe = od.pages[1]
    assert pe.method == METHOD and "Wren Ashdale" in pe.text and pe.words and all(w.bbox.x1 > w.bbox.x0 for w in pe.words)
    g = load_gold("doc_002")
    assert {e.page for e in restrict_gold(g, [1]).annotation.entities} == {1}

    class Exp:
        raw = {"detectors": [{"name": "baseline"}], "combiner": None}
    preds = runner.run_detector(runner.build_detector(Exp.raw), docs)
    priv = {"mentions": [{"id": "doc_002.e0007", "detected": False}],
            "attribution": [{"id": "doc_002.e0007", "outcome": "OCR_INDUCED_MISS"}]}
    out = runner._oracle_eval(Exp, docs, {"doc_002": g}, preds, {"doc_002": {1: "born_digital"}}, load_policy(), load_taxonomy(),
                              "dev", priv)
    assert out["pages"] == 1 and out["documents"] == ["doc_002"]
    assert out["oracle"]["overlap_any"]["tp"] >= 1                     # the baseline finds the name on perfect text
    # the name is protected on the oracle, so this OCR-induced miss stays an OCR miss
    assert priv["attribution"][0]["outcome"] == "OCR_INDUCED_MISS" and out["attribution_reclassified_to_detector_miss"] == 0
    # a detector that also misses it on perfect text: the miss is the detector's (plan §4.6)
    import pytest as _pt
    mp = _pt.MonkeyPatch()
    mp.setattr(runner, "detect", lambda exp, ds: ({d.document_id: [] for d in ds}, "none"))
    try:
        out2 = runner._oracle_eval(Exp, docs, {"doc_002": g}, preds, {"doc_002": {1: "born_digital"}}, load_policy(),
                                   load_taxonomy(), "dev", priv)
    finally:
        mp.undo()
    assert priv["attribution"][0]["outcome"] == "DETECTOR_MISS" and out2["attribution_reclassified_to_detector_miss"] == 1


def test_retest_namespace_pair_files_and_quality_table(repo):  # noqa: F811
    from redactor.cli_ext5 import iaa_stem
    from redactor.experiments.runner import _dataset_quality
    assert store.can_write("A1", "A1_retest") and not store.can_write("A2", "A1_retest")
    assert store.can_view("A1", "A1_retest", "doc_003") and not store.can_view("A2", "A1_retest", "doc_003")
    assert store.can_view("adjudicator", "A1_retest", "doc_003")
    assert iaa_stem("A1", "A2") == "iaa" and iaa_stem("claude_silver", "adjudicated") == "iaa.claude_silver__adjudicated"
    assert _dataset_quality() is None
    metrics = {"pages_compared": 5, "span": {"strict": {"a_as_gold": {"f1": 0.93}}, "overlap_any": {"a_as_gold": {"f1": 0.97}}},
               "kappa": {"protect_binary": 0.9}, "canonical_b3": {"f1": 0.95}}
    for a, b, kind in (("A1", "A2", "inter_annotator"), ("A1", "A1_retest", "test_retest")):
        write_canonical(repo / "golden_dataset" / "iaa" / f"{iaa_stem(a, b)}.public.json",
                        {"a": a, "b": b, "kind": kind, "documents": {"doc_003": metrics}})
    q = _dataset_quality()
    assert set(q) == {"A1 vs A2", "A1 vs A1_retest"} and q["A1 vs A1_retest"]["kind"] == "test_retest"
    assert q["A1 vs A2"]["documents"]["doc_003"] == {"pages": 5, "strict_f1": 0.93, "overlap_any_f1": 0.97, "kappa_protect": 0.9,
                                                     "b3_f1": 0.95}
