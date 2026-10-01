"""Page classifier on one synthetic PDF per class, and register behaviour (plan §8, §12)."""

from __future__ import annotations

import os
import stat

import pytest

from redactor.dataset import register as reg
from redactor.ingest.classifier import classify, signals_of
from redactor.ingest.pdfinfo import inspect_document
from redactor.synth import pdfs


@pytest.fixture(scope="module")
def synth_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("synth")
    lines = pdfs.filler_lines(30)
    pdfs.born_digital(d / "born.pdf", [lines])
    pdfs.scanned(d / "scan.pdf", [lines], skew_deg=0.8, noise=0.001)
    pdfs.vector_outlined(d / "vector.pdf", [lines])
    pdfs.hybrid(d / "hybrid.pdf", [lines[:20]], ["Letterhead line one", "Letterhead line two"])
    pdfs.blank(d / "blank.pdf")
    return d


@pytest.mark.parametrize("name,kind,scope", [
    ("born.pdf", "born_digital", "none"),
    ("scan.pdf", "scanned", "full_page"),
    ("vector.pdf", "vector_outlined", "full_page"),
    ("hybrid.pdf", "hybrid", "raster_regions"),
    ("blank.pdf", "blank", "none"),
])
def test_classifier_classes(synth_dir, name, kind, scope):
    (ps,) = inspect_document(synth_dir / name)
    c = classify(signals_of(ps))
    assert (c.content_kind, c.ocr_scope) == (kind, scope)
    if kind == "born_digital":
        assert c.text_layer == "usable"
    if kind in ("scanned", "vector_outlined"):
        assert c.text_layer == "absent"


def test_synthetic_builds_are_deterministic(tmp_path):
    lines = pdfs.filler_lines(5)
    a = pdfs.born_digital(tmp_path / "a.pdf", [lines]).read_bytes()
    b = pdfs.born_digital(tmp_path / "b.pdf", [lines]).read_bytes()
    assert a == b


def test_register_is_append_only_and_read_only(data_root, synth_dir, monkeypatch):
    src = data_root / "golden_dataset_docs"
    src.mkdir()
    for name in ("born.pdf", "scan.pdf"):
        (src / f"Some Person {name}").write_bytes((synth_dir / name).read_bytes())
    r1 = reg.register(src, enforce_expected=False)
    assert r1.new == ["doc_001", "doc_002"] and r1.documents == 2
    copy = reg.document_path("doc_001")
    assert not (os.stat(copy).st_mode & stat.S_IWRITE)
    manifest_text = reg.manifest_path().read_text(encoding="utf-8")
    assert "Some Person" not in manifest_text  # names never stored in the manifest
    (src / "Another vector.pdf").write_bytes((synth_dir / "vector.pdf").read_bytes())
    r2 = reg.register(src, enforce_expected=False)
    assert r2.new == ["doc_003"] and sorted(r2.already_registered) == ["doc_001", "doc_002"]
    assert reg.verify_hashes(src)["ok"]
    # a changed source file with a registered name is a hard failure
    os.chmod(src / "Some Person born.pdf", stat.S_IWRITE | stat.S_IREAD)
    (src / "Some Person born.pdf").write_bytes(b"%PDF-1.4 changed")
    with pytest.raises(reg.RegisterError):
        reg.register(src, enforce_expected=False)
