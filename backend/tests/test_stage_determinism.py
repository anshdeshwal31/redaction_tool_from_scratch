"""Stage-isolated determinism (plan §4.7 a, c–f) on synthetic data, in-process and in fresh processes with
different PYTHONHASHSEED values."""

from __future__ import annotations

import shutil

import pytest

from redactor.core.canonical import canonical_json, sha256_hex


def test_replacement_gate_outputs_pdf_and_reidentification_are_deterministic():
    from redactor.evaluation.stage_determinism import run
    out = run(in_process=2, seeds=(11, 23))
    assert set(out["stages"]) == {"replacement", "gate", "json", "edit_log_private", "text", "markdown", "pdf_bytes",
                                  "pdf_raster", "reidentify"}
    assert out["all_identical"], {k: v for k, v in out["stages"].items() if not v["identical"]}


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker not available")
def test_every_ocr_retry_rung_is_deterministic(data_root):
    """(a) OCR on a synthetic scanned page with every rung of the ladder, recomputed from scratch twice."""
    from redactor.dataset.register import register
    from redactor.extraction import attempts as att
    from redactor.extraction.ocr_tesseract import TesseractConfig, image_available
    from redactor.ingest.metadata import classify_all, load_metadata
    from redactor.paths import fixtures_dir
    if not image_available(TesseractConfig.load()):
        pytest.skip("tesseract image not built")
    src = data_root / "golden_dataset_docs"
    src.mkdir()
    shutil.copyfile(fixtures_dir() / "syn_scanned.pdf", src / "a.pdf")
    register(src, enforce_expected=False)
    classify_all()
    pg = load_metadata("doc_001").pages[0]

    def all_rungs() -> dict[str, str]:
        res = {}
        for name, settings in (("plain", {}), ("dpi400", {"dpi": 400}), ("deskew", {"deskew": True}), ("psm6", {"psm": 6}),
                               ("contrast", {"contrast": True})):
            res[name] = sha256_hex(canonical_json(att.tesseract_attempt("doc_001", 1, settings).to_dict()))
        for rot in (90, 270):
            res[f"rot{rot}"] = sha256_hex(canonical_json(att.rotation_attempt("doc_001", 1, rot, pg.width_pt, pg.height_pt).to_dict()))
        res["rapidocr"] = sha256_hex(canonical_json(att.rapid_attempt("doc_001", 1, pg.width_pt, pg.height_pt).to_dict()))
        return res

    first = all_rungs()
    for d in ("extraction", "renders"):
        shutil.rmtree(data_root / "data" / d, ignore_errors=True)     # force a full recomputation
    second = all_rungs()
    assert first == second, sorted(k for k in first if first[k] != second.get(k))
