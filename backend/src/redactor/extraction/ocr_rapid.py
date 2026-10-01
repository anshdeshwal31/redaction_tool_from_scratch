"""Second OCR engine: RapidOCR (ONNX, PP-OCRv4 models bundled in the wheel; plan §6, §11 G1).

Runs fully offline (models are files inside the installed package, hashed into the fingerprint) with
one ONNX thread. RapidOCR returns line boxes; word boxes are apportioned along the line by character
position, so projection and the union-of-attempts rule can use them.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from ..core.canonical import sha256_obj
from ..core.coords import pixel_box_to_points
from ..core.types import BBox, PageExtraction, RawWord, build_page_text
from . import cache
from .render import RENDER_INFO


@lru_cache(maxsize=1)
def _engine():
    import rapidocr_onnxruntime
    from rapidocr_onnxruntime import RapidOCR
    return RapidOCR(intra_op_num_threads=1, inter_op_num_threads=1), getattr(rapidocr_onnxruntime, "__version__", "1.4.4")


@lru_cache(maxsize=1)
def model_hashes() -> dict[str, str]:
    import rapidocr_onnxruntime
    root = Path(rapidocr_onnxruntime.__file__).parent
    return {m.name: hashlib.sha256(m.read_bytes()).hexdigest() for m in sorted(root.rglob("*.onnx"))}


def fingerprint(dpi: int) -> dict[str, Any]:
    _, ver = _engine()
    return {"engine": "rapidocr_onnxruntime", "version": ver, "models": model_hashes(), "threads": 1,
            "render": {**RENDER_INFO, "dpi": dpi, "mode": "gray"}}


def source_id(dpi: int = 300) -> str:
    _, ver = _engine()
    return f"ocr.rapidocr@{ver}+ppocrv4#{sha256_obj(fingerprint(dpi))[:8]}"


def words_from_lines(result: list, dpi: int, ox_pt: float = 0.0, oy_pt: float = 0.0) -> list[RawWord]:
    words: list[RawWord] = []
    for li, (quad, text, score) in enumerate(result or []):
        xs = [p[0] for p in quad]
        ys = [p[1] for p in quad]
        line = pixel_box_to_points(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys), dpi, ox_pt, oy_pt)
        toks = text.split()
        n = max(1, len(text))
        pos = 0
        for t in toks:
            i = text.index(t, pos)
            x0 = line.x0 + line.width * i / n
            x1 = line.x0 + line.width * (i + len(t)) / n
            words.append(RawWord(t, BBox(x0, line.y0, max(x1, x0 + 0.01), line.y1), round(float(score) * 100, 2), 1, li))
            pos = i + len(t)
    return words


def ocr_image(img: Image.Image, dpi: int) -> list[RawWord]:
    eng, _ = _engine()
    result, _ = eng(np.array(img.convert("RGB")))
    return words_from_lines(result, dpi)


def ocr_page(document_id: str, render_png: Path, page: int, width_pt: float, height_pt: float, dpi: int = 300) -> PageExtraction:
    sid = source_id(dpi)
    if cache.has_page(document_id, sid, page):
        return cache.load_page(document_id, sid, page)
    img = Image.open(render_png)
    img.load()
    text, words = build_page_text(ocr_image(img, dpi))
    pe = PageExtraction(document_id, page, sid, "ocr", width_pt, height_pt, words, text, fingerprint(dpi))
    cache.save_page(pe)
    return cache.load_page(document_id, sid, page)
