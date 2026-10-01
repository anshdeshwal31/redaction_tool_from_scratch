"""OCR retry-ladder attempts (plan §4.11): same engine with other settings, a rotation search for
pages scanned sideways (found in S1: doc_002's annexure pages), and the second engine. Every attempt is
a cached PageExtraction in displayed page space with its own text source ID and fingerprint."""

from __future__ import annotations

from typing import Any, Mapping

from PIL import Image

from ..core.canonical import sha256_file, sha256_obj
from ..core.coords import unrotate_box
from ..core.types import PageExtraction, RawWord, build_page_text
from ..dataset.register import document_path
from . import cache, ocr_rapid
from .base import OcrSettings, ocr_config
from .ocr_tesseract import TesseractConfig, fingerprint_dict, parse_tsv, run_batch, text_source_id
from .pipeline import ocr_pages, tsv_dir
from .render import png_bytes, render_cached, renders_dir


def quality(pe: PageExtraction, t_word: float) -> dict[str, Any]:
    confs = [w.conf for w in pe.words if w.conf is not None]
    if not confs:
        return {"words": len(pe.words), "mean": None, "low_frac": None}
    return {"words": len(confs), "mean": round(sum(confs) / len(confs), 2),
            "low_frac": round(sum(1 for c in confs if c < t_word) / len(confs), 4)}


def is_low(q: Mapping[str, Any], th: Mapping[str, Any]) -> bool:
    if q["mean"] is None:
        return q["words"] == 0 and th.get("empty_page_is_low", False)
    return q["mean"] < th["T_mean"] or q["low_frac"] > th["T_frac"]


def tesseract_attempt(document_id: str, page: int, settings: Mapping[str, Any]) -> PageExtraction:
    base = ocr_config()
    s = OcrSettings(dpi=int(settings.get("dpi", base["render"]["dpi"])), psm=int(settings.get("psm", base["tesseract"]["psm"])),
                    deskew=bool(settings.get("deskew", False)), contrast=bool(settings.get("contrast", False)))
    return ocr_pages(document_id, document_path(document_id), [page], s, TesseractConfig.load())[page]


def rotation_attempt(document_id: str, page: int, rot: int, width_pt: float, height_pt: float) -> PageExtraction:
    """OCR of the page turned upright by `rot` degrees clockwise; words keep the upright reading order,
    boxes are mapped back to the page."""
    cfg = TesseractConfig.load()
    base = ocr_config()
    settings = OcrSettings(dpi=int(base["render"]["dpi"]), psm=6)
    sid0 = text_source_id(cfg, settings)
    sid = f"{sid0}+rot{rot}"
    if cache.has_page(document_id, sid, page):
        return cache.load_page(document_id, sid, page)
    img = Image.open(render_cached(document_id, document_path(document_id), page, settings.dpi))
    img.load()
    turned = img.transpose(Image.Transpose.ROTATE_270 if rot == 90 else Image.Transpose.ROTATE_90)
    out = renders_dir(document_id, settings.dpi) / f"rot{rot}" / f"p{page:04d}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(png_bytes(turned))
    tsv = run_batch(cfg, [out], tsv_dir(document_id, sid), settings)[out]
    raw = parse_tsv(tsv.read_text(encoding="utf-8"), settings.dpi)
    mapped = [RawWord(w.text, unrotate_box(w.bbox, rot, width_pt, height_pt), w.conf, w.block, w.line) for w in raw]
    text, words = build_page_text(mapped)
    pe = PageExtraction(document_id, page, sid, "ocr", width_pt, height_pt, words, text,
                        {**fingerprint_dict(cfg, settings), "rotation_cw": rot, "tsv_sha256": sha256_file(tsv)})
    cache.save_page(pe)
    return cache.load_page(document_id, sid, page)


def rapid_attempt(document_id: str, page: int, width_pt: float, height_pt: float, dpi: int = 300) -> PageExtraction:
    png = render_cached(document_id, document_path(document_id), page, dpi)
    return ocr_rapid.ocr_page(document_id, png, page, width_pt, height_pt, dpi)


def rung_id(rung: Mapping[str, Any]) -> str:
    return f"{rung['name']}#{sha256_obj(dict(rung))[:8]}"
