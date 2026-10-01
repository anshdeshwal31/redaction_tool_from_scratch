"""Real-data extraction checks (plan §12). Aggregate assertions only.

The OCR determinism check re-runs Tesseract on 5 pages x 20 runs and compares TSV hashes.
It is slow (~10-15 min); select it with:  uv run pytest tests_local -k determinism
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from redactor.core.canonical import sha256_file
from redactor.extraction import cache
from redactor.extraction.base import OcrSettings
from redactor.extraction.ocr_tesseract import TesseractConfig, run_batch
from redactor.extraction.render import renders_dir

DOCS = ("doc_001", "doc_002", "doc_003", "doc_004", "doc_005")
SAMPLE = [("doc_001", 1), ("doc_002", 1), ("doc_002", 15), ("doc_004", 1), ("doc_004", 11)]


def test_every_page_has_a_reference_text_source():
    methods: dict[str, int] = {}
    pages = 0
    for doc in DOCS:
        ref = cache.load_reference(doc)
        pages += len(ref)
        for page, sid in ref.items():
            pe = cache.load_page(doc, sid, page)
            assert pe is not None and all(pe.text[w.start:w.end] == w.text for w in pe.words)
            methods[pe.method] = methods.get(pe.method, 0) + 1
    assert pages == 88
    assert methods["ocr"] == 61  # 52 scanned + 9 vector-outlined (plan §1.2)


@pytest.mark.skipif(os.environ.get("REDACTOR_SLOW") != "1", reason="set REDACTOR_SLOW=1 for the 100-run OCR determinism check")
def test_ocr_determinism_5_pages_x_20_runs():
    cfg = TesseractConfig.load()
    settings = OcrSettings()
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / "in"
        stage.mkdir()
        pngs = []
        for doc, page in SAMPLE:
            src = renders_dir(doc, settings.dpi) / f"p{page:04d}.png"
            dst = stage / f"{doc}_p{page:04d}.png"
            dst.write_bytes(src.read_bytes())
            pngs.append(dst)
        hashes: dict[str, set[str]] = {p.name: set() for p in pngs}
        for run in range(20):
            out = run_batch(cfg, pngs, Path(tmp) / f"out{run:02d}", settings)
            for png, tsv in out.items():
                hashes[png.name].add(sha256_file(tsv))
        assert all(len(v) == 1 for v in hashes.values()), {k: len(v) for k, v in hashes.items()}
