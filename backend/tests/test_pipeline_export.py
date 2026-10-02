"""End to end on synthetic PDFs (plan §12 "end to end"): register -> extract -> pipeline run (gate) ->
staged X1 JSON -> export, which refuses while the gate is not clear and succeeds once it is."""

from __future__ import annotations

from redactor.core.canonical import read_json
from redactor.dataset.register import register
from redactor.extraction.pipeline import extract_document
from redactor.ingest.metadata import classify_all
from redactor.output.export import verify_sha
from redactor.output.models import validate_document
from redactor.replacement.vault import Vault
from redactor.synth import pdfs

CLEAN = ["Re: Ms Alexandra Quillfeather", "Ms Quillfeather attended the clinic for review on 3 March 2024."]


def test_pipeline_stages_valid_outputs_and_export_follows_the_gate(data_root):
    from redactor import pipeline
    from redactor.output.exporter import export_matter
    src = data_root / "golden_dataset_docs"
    src.mkdir()
    pdfs.born_digital(src / "a.pdf", [CLEAN])
    pdfs.born_digital(src / "b.pdf", [CLEAN + ["Seen again by Ms Quillfeather.", "Unlabelled 123 456 782 appears here."]])
    register(src, enforce_expected=False)
    classify_all()
    for d in ("doc_001", "doc_002"):
        extract_document(d)
    vault = Vault.ephemeral()
    summary = pipeline.run_matter("matter_001", vault=vault, attempt_fn=lambda *a: [])
    assert summary["pages"] == 2
    st = pipeline.load_state("matter_001")
    for d in ("doc_001", "doc_002"):
        doc = read_json(pipeline.outputs_dir("matter_001") / f"{d}.export.json")
        assert validate_document(doc) and verify_sha(doc) and doc["output_sha256"] == st["export_sha256"][d]
        assert "Quillfeather" not in str(doc)
        assert all(e["canonical_id"] is None or e["canonical_id"].startswith("matter_001/") for e in doc["edit_log"])
    # doc_002 carries a checksum-valid identifier nobody labelled: BLOCKED, not exportable
    assert st["export_allowed"] == {"doc_001": True, "doc_002": False}
    rep = export_matter("matter_001", formats=["json", "text", "md"], vault=vault)
    assert rep["documents"]["doc_001"]["status"] == "exported"
    assert rep["documents"]["doc_002"]["status"] == "refused" and rep["documents"]["doc_002"]["reason"] == "gate_not_clear"
    out = sorted(p.name for p in (data_root / "exports" / "matter_001").iterdir())
    assert out == ["doc_001.md", "doc_001.pseudonymized.json", "doc_001.txt", "private"]
    # the staged output may not change between the gate run and the export
    sp = pipeline.outputs_dir("matter_001") / "doc_001.export.json"
    doc = read_json(sp)
    doc["pages"][0]["text"] += " "
    from redactor.core.canonical import write_canonical
    write_canonical(sp, doc)
    assert export_matter("matter_001", formats=["json"], vault=vault)["documents"]["doc_001"]["reason"] == "staged_output_changed"


def test_intake_registers_without_filename_and_is_never_scored(data_root):
    from redactor.dataset.intake import IntakeError, add_pdf, process, status
    from redactor.dataset.register import load_manifest
    from redactor.experiments.runner import dataset_documents
    src = data_root / "golden_dataset_docs"
    src.mkdir()
    pdfs.born_digital(src / "a.pdf", [CLEAN])
    register(src, enforce_expected=False)
    up = data_root / "Quillfeather_private_upload.pdf"
    pdfs.born_digital(up, [CLEAN + ["Second page line."]])
    rec = add_pdf(up.read_bytes(), "matter_001")
    assert rec == {"document_id": "doc_002", "new": True, "matter_id": "matter_001", "split": "intake"}
    assert add_pdf(up.read_bytes(), "matter_001")["new"] is False
    m = load_manifest().model_dump_json() if hasattr(load_manifest(), "model_dump_json") else str(load_manifest())
    assert "Quillfeather" not in m and "upload" not in m.lower().replace("upload:", "")
    assert status("doc_002")["state"] == "registered"
    out = process("doc_002")
    assert out["pages"] == 1 and status("doc_002")["state"] == "extracted"
    assert [d for d, _ in dataset_documents(["dev", "test"])] == ["doc_001"]
    import pytest
    with pytest.raises(IntakeError):
        add_pdf(b"not a pdf", "matter_001")


def test_preview_shows_burned_page_and_pseudonymised_text(data_root):
    from redactor import pipeline
    from redactor.api.routes.outputs import preview, preview_image
    src = data_root / "golden_dataset_docs"
    src.mkdir()
    pdfs.born_digital(src / "a.pdf", [CLEAN])
    register(src, enforce_expected=False)
    classify_all()
    extract_document("doc_001")
    pipeline.run_matter("matter_001", vault=Vault.ephemeral(), attempt_fn=lambda *a: [])
    pv = preview("matter_001", "doc_001")
    assert pv["pages"][0]["spans"] >= 2 and "Quillfeather" not in pv["pages"][0]["text"]
    img = preview_image("matter_001", "doc_001", 1, dpi=72)
    assert img.media_type == "image/png" and img.body.startswith(b"\x89PNG")


def test_export_becomes_stale_when_the_vault_epoch_is_bumped(data_root):
    from redactor import pipeline
    from redactor.output.exporter import export_matter, export_status
    src = data_root / "golden_dataset_docs"
    src.mkdir()
    pdfs.born_digital(src / "a.pdf", [CLEAN])
    register(src, enforce_expected=False)
    classify_all()
    extract_document("doc_001")
    vault = Vault.ephemeral()
    pipeline.run_matter("matter_001", vault=vault, attempt_fn=lambda *a: [])
    assert export_matter("matter_001", formats=["json"], vault=vault)["exported"] == 1
    st = export_status("matter_001", vault.epoch)
    assert st["documents"]["doc_001"] == {"exported_epoch": 1, "stale": False} and st["stale"] == []
    vault.bump_epoch()                                   # a surrogate was re-issued (plan §4.9.5)
    assert export_status("matter_001", vault.epoch)["stale"] == ["doc_001"]
    pipeline.run_matter("matter_001", vault=vault, attempt_fn=lambda *a: [])
    assert pipeline.load_state("matter_001")["stale_exports"] == ["doc_001"]
    assert export_matter("matter_001", formats=["json"], vault=vault)["exported"] == 1   # re-export clears it
    assert export_status("matter_001", vault.epoch)["stale"] == []


def test_extractions_routes_list_sources_and_words(data_root):
    import pytest
    from fastapi import HTTPException
    from redactor.api.routes.documents import extraction_sources, extraction_words
    src = data_root / "golden_dataset_docs"
    src.mkdir()
    pdfs.born_digital(src / "a.pdf", [CLEAN])
    register(src, enforce_expected=False)
    classify_all()
    extract_document("doc_001")
    out = extraction_sources("doc_001")
    ref = out["reference"][1]
    assert ref in out["sources"] and out["sources"][ref]["pages"] == [1]
    w = extraction_words("doc_001", 1, source=ref)
    assert w["text_source_id"] == ref and len(w["words"]) > 5
    with pytest.raises(HTTPException):
        extraction_words("doc_001", 1, source="ocr.none#0")


def test_page_class_verification_and_e0(data_root):
    import pytest
    from redactor.dataset import page_classes as pc
    from redactor.experiments.runner import page_classes_for
    from redactor.ingest.metadata import load_metadata
    src = data_root / "golden_dataset_docs"
    src.mkdir()
    pdfs.born_digital(src / "a.pdf", [CLEAN, ["Second page."]])
    register(src, enforce_expected=False)
    classify_all()
    assert pc.e0_metrics(["doc_001"])["status"] == "not_started"
    cls1 = load_metadata("doc_001").pages[0].classification.content_kind
    assert pc.record("doc_001", 1, cls1, verified_by="AB")["agrees"]
    other = "hybrid" if cls1 != "hybrid" else "scanned"
    out = pc.record("doc_001", 2, other, verified_by="AB")
    assert not out["agrees"] and out["verified_pages"] == 2
    e0 = pc.e0_metrics(["doc_001"])
    assert e0["accuracy"] == 0.5 and e0["verified_pages"] == 2 and e0["status"] == "complete"
    assert page_classes_for("doc_001")[2] == other                      # breakdowns use the verified class
    classify_all()                                                      # re-classifying never erases verifications
    assert pc.verified_classes("doc_001") == {1: cls1, 2: other}
    with pytest.raises(pc.PageClassError):
        pc.record("doc_001", 1, "nonsense", verified_by="AB")
    with pytest.raises(pc.PageClassError):
        pc.record("doc_001", 9, cls1, verified_by="AB")
