"""Structural PDF inspection with pypdfium2 (plan §6: port of the planning-session inspection).

Only structure leaves this module as signals: counts, geometry, render modes, ratios. Page text
is read to compute character statistics but is never returned or logged here.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass, field
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

from ..core.coords import PageGeometry
from ..core.types import BBox

PDFIUM_VERSION = str(getattr(pdfium, "PDFIUM_INFO", getattr(pdfium, "V_PDFIUM", "unknown")))
PYPDFIUM2_VERSION = str(getattr(pdfium, "PYPDFIUM_INFO", getattr(pdfium, "V_PYPDFIUM2", "unknown")))


@dataclass
class RasterRegion:
    bbox: BBox          # displayed page space, clipped to the page
    px_width: int
    px_height: int
    dpi: int


@dataclass
class PageStructure:
    page: int
    geometry: PageGeometry
    width_pt: float
    height_pt: float
    rotation: int
    visible_text_chars: int = 0
    invisible_text_chars: int = 0
    alnum_ratio: float | None = None
    bad_char_ratio: float | None = None
    image_regions: list[RasterRegion] = field(default_factory=list)
    image_coverage: float = 0.0
    vector_paths: int = 0
    small_filled_paths: int = 0


def page_geometry(page: pdfium.PdfPage) -> PageGeometry:
    left, bottom, right, top = page.get_cropbox()
    return PageGeometry(right - left, top - bottom, page.get_rotation(), left, bottom)


def _text_obj_chars(obj, textpage) -> int:
    try:
        n = pdfium_c.FPDFTextObj_GetText(obj.raw, textpage.raw, None, 0)
        if n <= 2:
            return 0
        buf = ctypes.create_string_buffer(n)
        pdfium_c.FPDFTextObj_GetText(obj.raw, textpage.raw, ctypes.cast(buf, ctypes.POINTER(pdfium_c.FPDF_WCHAR)), n)
        text = buf.raw[: n - 2].decode("utf-16-le", "ignore")
        return sum(1 for ch in text if not ch.isspace())
    except Exception:  # noqa: BLE001 - structural signal only
        return 0


def _is_bad_char(ch: str) -> bool:
    cp = ord(ch)
    return ch == "�" or 0xE000 <= cp <= 0xF8FF


def _coverage(regions: list[BBox], w: float, h: float, grid: int) -> float:
    if not regions or w <= 0 or h <= 0:
        return 0.0
    hits = 0
    for gx in range(grid):
        x = (gx + 0.5) * w / grid
        for gy in range(grid):
            y = (gy + 0.5) * h / grid
            if any(r.x0 <= x <= r.x1 and r.y0 <= y <= r.y1 for r in regions):
                hits += 1
    return hits / (grid * grid)


def inspect_page(pdf: pdfium.PdfDocument, index: int, *, grid: int = 40, small_path_max_pt: float = 20.0) -> PageStructure:
    page = pdf[index]
    geom = page_geometry(page)
    w, h = geom.display_size
    ps = PageStructure(page=index + 1, geometry=geom, width_pt=w, height_pt=h, rotation=geom.rotation)
    textpage = page.get_textpage()
    try:
        n = textpage.count_chars()
        text = textpage.get_text_range(0, n) if n else ""
        nonspace = [ch for ch in text if not ch.isspace()]
        invisible = 0
        page_box = BBox(0, 0, w, h)
        for obj in page.get_objects(max_depth=15):
            t = obj.type
            if t == pdfium_c.FPDF_PAGEOBJ_TEXT:
                if pdfium_c.FPDFTextObj_GetTextRenderMode(obj.raw) == pdfium_c.FPDF_TEXTRENDERMODE_INVISIBLE:
                    invisible += _text_obj_chars(obj, textpage)
            elif t == pdfium_c.FPDF_PAGEOBJ_IMAGE:
                l, b, r, tp = obj.get_pos()
                box = geom.user_box_to_display(l, b, r, tp)
                clipped = BBox(max(0.0, box.x0), max(0.0, box.y0), min(w, box.x1), min(h, box.y1)) \
                    if box.x1 > 0 and box.y1 > 0 and box.x0 < w and box.y0 < h else None
                if clipped is None or clipped.area <= 0:
                    continue
                try:
                    pw, ph = obj.get_size()
                except Exception:  # noqa: BLE001
                    pw, ph = 0, 0
                dpi = int(round(pw / (box.width / 72.0))) if box.width > 0 and pw else 0
                ps.image_regions.append(RasterRegion(clipped, int(pw), int(ph), dpi))
            elif t == pdfium_c.FPDF_PAGEOBJ_PATH:
                ps.vector_paths += 1
                l, b, r, tp = obj.get_pos()
                if (r - l) < small_path_max_pt and (tp - b) < small_path_max_pt:
                    fill = ctypes.c_int(0)
                    stroke = ctypes.c_int(0)
                    ok = pdfium_c.FPDFPath_GetDrawMode(obj.raw, ctypes.byref(fill), ctypes.byref(stroke))
                    if not ok or fill.value != 0:
                        ps.small_filled_paths += 1
        ps.invisible_text_chars = min(invisible, len(nonspace))
        ps.visible_text_chars = len(nonspace) - ps.invisible_text_chars
        if nonspace:
            ps.alnum_ratio = sum(1 for ch in nonspace if ch.isalnum()) / len(nonspace)
            ps.bad_char_ratio = sum(1 for ch in nonspace if _is_bad_char(ch)) / len(nonspace)
        ps.image_coverage = _coverage([r.bbox for r in ps.image_regions], w, h, grid)
        del page_box
    finally:
        textpage.close()
        page.close()
    return ps


def inspect_document(path: str | Path, *, grid: int = 40, small_path_max_pt: float = 20.0) -> list[PageStructure]:
    pdf = pdfium.PdfDocument(str(path))
    try:
        return [inspect_page(pdf, i, grid=grid, small_path_max_pt=small_path_max_pt) for i in range(len(pdf))]
    finally:
        pdf.close()
