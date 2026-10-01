"""Deterministic page rendering with pdfium (plan §4.7): pinned renderer, fixed DPI, grayscale."""

from __future__ import annotations

import io
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageOps

from ..core.coords import points_box_to_pixels
from ..core.types import BBox
from ..ingest.pdfinfo import PDFIUM_VERSION
from ..paths import data_dir


def render_page(pdf_path: str | Path, page: int, dpi: int = 300, *, gray: bool = True) -> Image.Image:
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        p = pdf[page - 1]
        try:
            bitmap = p.render(scale=dpi / 72.0, grayscale=gray, fill_color=(255, 255, 255, 255),
                              draw_annots=True, may_draw_forms=True)
            img = bitmap.to_pil()
            img.load()
            return img.convert("L") if gray else img.convert("RGB")
        finally:
            p.close()
    finally:
        pdf.close()


def png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False, compress_level=6)
    return buf.getvalue()


def renders_dir(document_id: str, dpi: int) -> Path:
    return data_dir() / "renders" / document_id / str(dpi)


def render_cached(document_id: str, pdf_path: Path, page: int, dpi: int = 300) -> Path:
    out = renders_dir(document_id, dpi) / f"p{page:04d}.png"
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp")
        tmp.write_bytes(png_bytes(render_page(pdf_path, page, dpi)))
        tmp.replace(out)
    return out


def crop_region(page_png: Path, box: BBox, dpi: int, margin_pt: float, out: Path) -> tuple[Path, float, float]:
    """Crop a display-space box (plus margin) from a rendered page. Returns (path, x0_pt, y0_pt)."""
    img = Image.open(page_png)
    img.load()
    grown = BBox(max(0.0, box.x0 - margin_pt), max(0.0, box.y0 - margin_pt), box.x1 + margin_pt, box.y1 + margin_pt)
    l, t, r, b = points_box_to_pixels(grown, dpi)
    r, b = min(r, img.width), min(b, img.height)
    crop = img.crop((l, t, r, b))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(png_bytes(crop))
    return out, l * 72.0 / dpi, t * 72.0 / dpi


def preprocess(img: Image.Image, *, deskew: bool, contrast: bool) -> Image.Image:
    """Retry-ladder preprocessing (plan §4.11), deterministic."""
    out = img
    if contrast:
        out = ImageOps.autocontrast(out, cutoff=1)
    if deskew:
        out = _deskew(out)
    return out


def _deskew(img: Image.Image) -> Image.Image:
    """Projection-profile deskew over ±3° in 0.25° steps (deterministic, numpy only)."""
    import numpy as np
    small = img.convert("L").resize((max(1, img.width // 4), max(1, img.height // 4)))
    arr = (np.asarray(small) < 128).astype(np.float32)
    best_angle, best_score = 0.0, -1.0
    for step in range(-12, 13):
        angle = step * 0.25
        rotated = Image.fromarray((arr * 255).astype("uint8")).rotate(angle, fillcolor=0)
        profile = np.asarray(rotated, dtype=np.float32).sum(axis=1)
        score = float(np.var(profile))
        if score > best_score + 1e-9:
            best_angle, best_score = angle, score
    if best_angle == 0.0:
        return img
    return img.rotate(best_angle, resample=Image.BICUBIC, fillcolor=255)


RENDER_INFO = {"engine": "pdfium", "version": PDFIUM_VERSION}
