"""Extraction pipeline (plan §4.1 S1): route each page by its class, cache every text source.

- ocr_scope none            -> text layer (pdfium)
- ocr_scope full_page       -> render, then Tesseract on the rendered page (never on extracted images)
- ocr_scope raster_regions  -> hybrid: text layer + Tesseract on each raster region, overlapping words dropped
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.canonical import read_json, sha256_file, sha256_obj, write_canonical
from ..core.types import PageExtraction, build_page_text
from ..dataset.models import DocumentMetadata, ExtractionInfo, RenderInfo
from ..dataset.register import document_path, load_manifest
from ..ingest.metadata import metadata_path
from ..ingest.pdfinfo import PDFIUM_VERSION, inspect_document
from ..paths import data_dir
from . import cache, text_layer
from .base import OcrSettings, ocr_config
from .ocr_tesseract import (TesseractConfig, fingerprint_dict, parse_tsv, prepare_input, run_batch,
                            text_source_id as ocr_source_id)
from .render import crop_region, render_cached, renders_dir


def hybrid_source_id(cfg: TesseractConfig, settings: OcrSettings) -> str:
    parts = {"text_layer": text_layer.text_source_id(), "ocr": ocr_source_id(cfg, settings), "hybrid": ocr_config()["hybrid"]}
    return f"hybrid.pdfium+tesseract@{cfg.version}#{sha256_obj(parts)[:8]}"


def tsv_dir(document_id: str, source_id: str) -> Path:
    return data_dir() / "ocr_tsv" / document_id / cache.source_key(source_id)


def _load_meta(document_id: str) -> DocumentMetadata:
    return DocumentMetadata.model_validate(read_json(metadata_path(document_id)))


def ocr_pages(document_id: str, pdf: Path, pages: list[int], settings: OcrSettings,
              cfg: TesseractConfig, *, force: bool = False) -> dict[int, PageExtraction]:
    """Full-page OCR for the given pages (cached per text source)."""
    sid = ocr_source_id(cfg, settings)
    out: dict[int, PageExtraction] = {}
    todo = []
    for page in pages:
        if not force and cache.has_page(document_id, sid, page):
            out[page] = cache.load_page(document_id, sid, page)
            continue
        render = render_cached(document_id, pdf, page, settings.dpi)
        pre_dir = renders_dir(document_id, settings.dpi) / f"pre_{sha256_obj(settings.to_dict())[:8]}"
        todo.append((page, prepare_input(render, pre_dir / render.name, settings)))
    by_dir: dict[Path, list[tuple[int, Path]]] = {}
    for page, png in todo:
        by_dir.setdefault(png.parent, []).append((page, png))
    engine = fingerprint_dict(cfg, settings)
    for _, items in sorted(by_dir.items(), key=lambda kv: str(kv[0])):
        tsvs = run_batch(cfg, [png for _, png in items], tsv_dir(document_id, sid), settings)
        for page, png in items:
            raw = parse_tsv(tsvs[png].read_text(encoding="utf-8"), settings.dpi)
            text, words = build_page_text(raw)
            meta_page = _load_meta(document_id).pages[page - 1]
            pe = PageExtraction(document_id, page, sid, "ocr", meta_page.width_pt, meta_page.height_pt, words, text,
                                {**engine, "input_png_sha256": sha256_file(png), "tsv_sha256": sha256_file(tsvs[png])})
            cache.save_page(pe)
            out[page] = cache.load_page(document_id, sid, page)
    return out


def hybrid_pages(document_id: str, pdf: Path, pages: list[int], settings: OcrSettings,
                 cfg: TesseractConfig, *, force: bool = False) -> dict[int, PageExtraction]:
    hcfg = ocr_config()["hybrid"]
    sid = hybrid_source_id(cfg, settings)
    ocr_sid = ocr_source_id(cfg, settings)
    out: dict[int, PageExtraction] = {}
    structures = {ps.page: ps for ps in inspect_document(pdf)}
    crops: list[tuple[int, int, Path, float, float]] = []
    for page in pages:
        if not force and cache.has_page(document_id, sid, page):
            out[page] = cache.load_page(document_id, sid, page)
            continue
        ps = structures[page]
        area = ps.width_pt * ps.height_pt
        regions = sorted((r for r in ps.image_regions if r.bbox.area / area >= hcfg["raster_region_min_fraction"]),
                         key=lambda r: (round(r.bbox.y0, 1), round(r.bbox.x0, 1)))
        render = render_cached(document_id, pdf, page, settings.dpi)
        for k, region in enumerate(regions):
            dst = renders_dir(document_id, settings.dpi) / "regions" / f"p{page:04d}_r{k:02d}.png"
            path, ox, oy = crop_region(render, region.bbox, settings.dpi, hcfg["region_margin_pt"], dst)
            crops.append((page, k, path, ox, oy))
    tsvs = run_batch(cfg, [c[2] for c in crops], tsv_dir(document_id, ocr_sid) / "regions", settings) if crops else {}
    engine = {"text_layer": {"engine": "pdfium", "version": PDFIUM_VERSION, "config": text_layer.CONFIG},
              "ocr": fingerprint_dict(cfg, settings), "hybrid": hcfg}
    for page in pages:
        if page in out:
            continue
        tl_words, w, h = text_layer.raw_words(pdf, page)
        extra = []
        page_crops = [c for c in crops if c[0] == page]
        for _, k, path, ox, oy in page_crops:
            for rw in parse_tsv(tsvs[path].read_text(encoding="utf-8"), settings.dpi,
                                offset_x_pt=ox, offset_y_pt=oy, block_base=10000 * (k + 1)):
                covered = max((rw.bbox.overlap_fraction(t.bbox) for t in tl_words), default=0.0)
                if covered < hcfg["drop_overlap_fraction"]:
                    extra.append(rw)
        text, words = build_page_text(tl_words + extra)
        pe = PageExtraction(document_id, page, sid, "hybrid", round(w, 3), round(h, 3), words, text,
                            {**engine, "regions": len(page_crops),
                             "region_tsv_sha256": [sha256_file(tsvs[c[2]]) for c in page_crops]})
        cache.save_page(pe)
        out[page] = cache.load_page(document_id, sid, page)
    return out


def extract_document(document_id: str, *, force: bool = False, settings: OcrSettings | None = None) -> dict[str, Any]:
    settings = settings or OcrSettings(dpi=int(ocr_config()["render"]["dpi"]), psm=int(ocr_config()["tesseract"]["psm"]))
    cfg = TesseractConfig.load()
    pdf = document_path(document_id)
    meta = _load_meta(document_id)
    scopes = {p.page: p.classification.ocr_scope for p in meta.pages}
    reference: dict[int, str] = {}
    results: dict[int, PageExtraction] = {}
    tl_sid = text_layer.text_source_id()
    for page, scope in sorted(scopes.items()):
        if scope == "none":
            if force or not cache.has_page(document_id, tl_sid, page):
                cache.save_page(text_layer.extract(document_id, pdf, page))
            results[page] = cache.load_page(document_id, tl_sid, page)
    results.update(ocr_pages(document_id, pdf, [p for p, s in scopes.items() if s == "full_page"], settings, cfg, force=force))
    results.update(hybrid_pages(document_id, pdf, [p for p, s in scopes.items() if s == "raster_regions"], settings, cfg, force=force))
    render = RenderInfo(engine="pdfium", version=PDFIUM_VERSION, dpi=settings.dpi, mode="gray")
    for pm in meta.pages:
        pe = results[pm.page]
        reference[pm.page] = pe.text_source_id
        if pe.method == "text_layer":
            pm.extraction = ExtractionInfo(method="text_layer", text_source_id=pe.text_source_id, engine="pdfium",
                                           engine_version=PDFIUM_VERSION)
        else:
            pm.extraction = ExtractionInfo(method=pe.method, text_source_id=pe.text_source_id,
                                           engine="tesseract" if pe.method == "ocr" else "pdfium+tesseract",
                                           engine_version=cfg.version, model_sha256=cfg.model_sha256, render=render)
    write_canonical(metadata_path(document_id), meta.dump())
    cache.save_reference(document_id, reference)
    methods: dict[str, int] = {}
    confs = []
    for pe in results.values():
        methods[pe.method] = methods.get(pe.method, 0) + 1
        c = pe.mean_conf()
        if c is not None:
            confs.append(c)
    return {"pages": len(results), "methods": dict(sorted(methods.items())),
            "words": sum(len(pe.words) for pe in results.values()),
            "mean_ocr_conf": round(sum(confs) / len(confs), 2) if confs else None,
            "min_page_ocr_conf": round(min(confs), 2) if confs else None,
            "sources": sorted({pe.text_source_id for pe in results.values()})}


def extract_all(*, force: bool = False) -> dict[str, Any]:
    out = {}
    for d in load_manifest().documents:
        out[d.document_id] = extract_document(d.document_id, force=force)
    return out
