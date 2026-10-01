"""Synthetic fixtures with exact ground truth (plan §11 F5): determinism, projection, and v0 vs v1 on a form."""

from __future__ import annotations

import shutil

import pytest

from redactor.core.canonical import read_json, sha256_file
from redactor.core.types import DocumentText
from redactor.dataset.models import AnnotationSet
from redactor.detectors.base import create, run_detector
from redactor.evaluation.evaluate import evaluate_split
from redactor.evaluation.gold import GoldDocument, project_document
from redactor.extraction import text_layer
from redactor.extraction.ocr_tesseract import TesseractConfig, image_available
from redactor.synth import fixtures
from redactor.taxonomy import load_policy, load_taxonomy

POLICY, TAX = load_policy(), load_taxonomy()
needs_docker = pytest.mark.skipif(not (shutil.which("docker") and image_available(TesseractConfig.load())),
                                  reason="pinned Tesseract image not available")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    d = tmp_path_factory.mktemp("syn")
    fixtures.build_all(d)
    return d


def _gold(d, doc_id) -> GoldDocument:
    return GoldDocument(doc_id, AnnotationSet.model_validate(read_json(d / f"{doc_id}.truth.json")), "verified", "<fixture>")


def test_fixtures_are_byte_deterministic(built, tmp_path):
    fixtures.build_all(tmp_path)
    for f in sorted(p.name for p in built.iterdir()):
        assert sha256_file(built / f) == sha256_file(tmp_path / f), f


@pytest.mark.parametrize("doc_id", ["syn_born_digital", "syn_form", "syn_hybrid"])
def test_truth_projects_exactly_on_text_layer(built, doc_id):
    pe = text_layer.extract(doc_id, built / f"{doc_id}.pdf", 1)
    gold = _gold(built, doc_id)
    doc = DocumentText(doc_id, {1: pe}, {})
    mentions = project_document(gold, doc, policy=POLICY, taxonomy=TAX)
    text_layer_mentions = [m for m in mentions if m.text in pe.text]
    assert text_layer_mentions, "no text-layer mentions"
    for m in text_layer_mentions:
        assert m.projected and pe.text[m.start:m.end] == m.text, (m.entity_type, m.start, m.end)
    if doc_id == "syn_hybrid":  # the raster letterhead is invisible to the text layer
        assert any(not m.projected for m in mentions)


def test_v1_layout_rules_beat_v0_on_shuffled_form(built):
    pe = text_layer.extract("syn_form", built / "syn_form.pdf", 1)
    doc = DocumentText("syn_form", {1: pe}, {})
    gold = {"syn_form": _gold(built, "syn_form")}
    scores = {}
    for name in ("baseline", "baseline_v1"):
        preds = run_detector(create(name), [doc])
        pub, _, _ = evaluate_split([doc], gold, preds, page_classes={"syn_form": {1: "born_digital"}},
                                   policy=POLICY, taxonomy=TAX, split="dev", bootstrap_rounds=10)
        scores[name] = pub["detection"]["overlap_typed"]["micro"]["tp"]
    assert scores["baseline_v1"] >= scores["baseline"]


@needs_docker
@pytest.mark.parametrize("doc_id", ["syn_scanned", "syn_vector"])
def test_truth_regions_land_on_ocr_words(built, doc_id, data_root):
    from redactor.extraction.base import OcrSettings
    from redactor.extraction.ocr_tesseract import parse_tsv, run_batch
    from redactor.extraction.render import png_bytes, render_page
    from redactor.core.types import PageExtraction, build_page_text
    cfg = TesseractConfig.load()
    png = data_root / "in" / f"{doc_id}.png"
    png.parent.mkdir(parents=True)
    png.write_bytes(png_bytes(render_page(built / f"{doc_id}.pdf", 1, 300)))
    tsv = run_batch(cfg, [png], data_root / "out", OcrSettings(dpi=300, psm=3))[png]
    text, words = build_page_text(parse_tsv(tsv.read_text(encoding="utf-8"), 300))
    pe = PageExtraction(doc_id, 1, "ocr.test", "ocr", 595.28, 841.89, words, text, {})
    mentions = project_document(_gold(built, doc_id), DocumentText(doc_id, {1: pe}, {}), policy=POLICY, taxonomy=TAX)
    exact = sum(1 for m in mentions if m.projected and pe.text[m.start:m.end] == m.text)
    assert exact >= 0.8 * len(mentions), (exact, len(mentions))
