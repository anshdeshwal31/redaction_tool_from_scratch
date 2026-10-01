"""Extraction on synthetic PDFs: text layer, Docker Tesseract OCR, hybrid merge, determinism."""

from __future__ import annotations

import shutil

import pytest

from redactor.dataset.register import register
from redactor.extraction import cache
from redactor.extraction.base import OcrSettings
from redactor.extraction.ocr_tesseract import TesseractConfig, image_available
from redactor.extraction.pipeline import extract_document, ocr_pages
from redactor.ingest.metadata import classify_all
from redactor.synth import pdfs

needs_docker = pytest.mark.skipif(not (shutil.which("docker") and image_available(TesseractConfig.load())),
                                  reason="pinned Tesseract image not available")

LINES = ["Synthetic claimant Alex Sample attended on 3 March 2024.",
         "The treating clinic reviewed the synthetic injury report.",
         "Contact number 0491 570 156 was recorded for the file."] * 3


@pytest.fixture()
def registered(data_root):
    src = data_root / "golden_dataset_docs"
    src.mkdir()
    pdfs.born_digital(src / "a born.pdf", [LINES])
    pdfs.scanned(src / "b scan.pdf", [LINES], dpi=300)
    pdfs.hybrid(src / "c hybrid.pdf", [LINES], ["Letterhead Sample Clinic", "Phone 0491 570 157"])
    register(src, enforce_expected=False)
    classify_all()
    return data_root


def test_text_layer_offsets(registered):
    extract_document("doc_001")
    pe = cache.load_reference_pages("doc_001")[1]
    assert pe.method == "text_layer"
    assert "Alex Sample" in pe.text and "0491 570 156" in pe.text
    assert all(pe.text[w.start:w.end] == w.text for w in pe.words)
    assert all(0 <= w.bbox.x0 < w.bbox.x1 <= pe.width_pt and 0 <= w.bbox.y0 < w.bbox.y1 <= pe.height_pt for w in pe.words)


@needs_docker
def test_ocr_and_hybrid_and_determinism(registered):
    summary = extract_document("doc_002")
    assert summary["methods"] == {"ocr": 1}
    pe = cache.load_reference_pages("doc_002")[1]
    assert "Sample" in pe.text and "claimant" in pe.text
    assert pe.mean_conf() is not None and pe.mean_conf() > 80
    # OCR word boxes land where the text layer puts the same words (born-digital twin of the page)
    first = next(w for w in pe.words if w.text == "Synthetic")
    assert 50 <= first.bbox.x0 <= 62 and 60 <= first.bbox.y1 <= 76

    extract_document("doc_003")
    hy = cache.load_reference_pages("doc_003")[1]
    assert hy.method == "hybrid"
    assert "Letterhead" in hy.text and "Alex Sample" in hy.text

    cfg = TesseractConfig.load()
    settings = OcrSettings(dpi=300, psm=3)
    from redactor.dataset.register import document_path
    again = ocr_pages("doc_002", document_path("doc_002"), [1], settings, cfg, force=True)[1]
    assert again.to_dict() == pe.to_dict()
