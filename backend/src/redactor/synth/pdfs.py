"""Synthetic PDF builders, one per page class (born-digital, scanned, vector-outlined, hybrid, blank).

Deterministic: fixed fonts (reportlab's bundled Vera.ttf), fixed geometry, seeded noise, and the
reportlab canvas is created with `invariant=1` so repeated builds give identical bytes.
"""

from __future__ import annotations

import io
import os
import random
from pathlib import Path
from typing import Sequence

import reportlab
from fontTools.pens.basePen import BasePen
from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as rl_canvas

PAGE_W, PAGE_H = A4
FONT_PATH = os.path.join(os.path.dirname(reportlab.__file__), "fonts", "Vera.ttf")
LEFT = 56.0
TOP = 72.0
LEADING = 16.0
FONT_SIZE = 11.0


def _canvas(path: Path) -> rl_canvas.Canvas:
    path.parent.mkdir(parents=True, exist_ok=True)
    c = rl_canvas.Canvas(str(path), pagesize=A4, invariant=1, pageCompression=1)
    c.setTitle("")
    c.setAuthor("")
    c.setProducer("redactor-synth")
    return c


def line_positions(n: int) -> list[tuple[float, float]]:
    """Top-left (x, y) baseline positions in displayed page space for each line."""
    return [(LEFT, TOP + i * LEADING) for i in range(n)]


def born_digital(path: Path, pages: Sequence[Sequence[str]]) -> Path:
    c = _canvas(path)
    for lines in pages:
        c.setFont("Helvetica", FONT_SIZE)
        for (x, y), line in zip(line_positions(len(lines)), lines):
            c.drawString(x, PAGE_H - y, line)
        c.showPage()
    c.save()
    return path


def _page_image(lines: Sequence[str], dpi: int = 200) -> Image.Image:
    scale = dpi / 72.0
    img = Image.new("L", (int(PAGE_W * scale), int(PAGE_H * scale)), 255)
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(FONT_PATH, int(round(FONT_SIZE * scale)))
    for (x, y), line in zip(line_positions(len(lines)), lines):
        # PIL draws from the top of the text box; shift up so the baseline sits at y.
        draw.text((x * scale, (y - FONT_SIZE * 0.8) * scale), line, fill=0, font=font)
    return img


def scanned(path: Path, pages: Sequence[Sequence[str]], *, dpi: int = 200, skew_deg: float = 0.0,
            noise: float = 0.0, seed: int = 7) -> Path:
    """Each page is one full-page raster image, optionally skewed and noisy (seeded)."""
    rng = random.Random(seed)
    c = _canvas(path)
    for lines in pages:
        img = _page_image(lines, dpi)
        if skew_deg:
            img = img.rotate(skew_deg, resample=Image.BICUBIC, fillcolor=255)
        if noise:
            px = img.load()
            w, h = img.size
            for _ in range(int(w * h * noise)):
                x, y = rng.randrange(w), rng.randrange(h)
                px[x, y] = 0 if px[x, y] > 128 else 255
            img = img.filter(ImageFilter.MedianFilter(1))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        c.drawImage(ImageReader(buf), 0, 0, width=PAGE_W, height=PAGE_H)
        c.showPage()
    c.save()
    return path


class _CanvasPen(BasePen):
    def __init__(self, glyph_set, path, scale: float, ox: float, oy: float):
        super().__init__(glyph_set)
        self.p = path
        self.s = scale
        self.ox = ox
        self.oy = oy

    def _t(self, pt):
        return self.ox + pt[0] * self.s, self.oy + pt[1] * self.s

    def _moveTo(self, pt):
        self.p.moveTo(*self._t(pt))

    def _lineTo(self, pt):
        self.p.lineTo(*self._t(pt))

    def _curveToOne(self, p1, p2, p3):
        self.p.curveTo(*self._t(p1), *self._t(p2), *self._t(p3))

    def _closePath(self):
        self.p.close()


def vector_outlined(path: Path, pages: Sequence[Sequence[str]]) -> Path:
    """Text drawn as filled glyph outlines (no fonts, no text layer), like 'Microsoft Print to PDF'."""
    font = TTFont(FONT_PATH)
    glyph_set = font.getGlyphSet()
    cmap = font.getBestCmap()
    upm = font["head"].unitsPerEm
    hmtx = font["hmtx"]
    scale = FONT_SIZE / upm
    c = _canvas(path)
    for lines in pages:
        for (x, y), line in zip(line_positions(len(lines)), lines):
            cx = x
            for ch in line:
                gname = cmap.get(ord(ch))
                if gname is None:
                    cx += FONT_SIZE * 0.5
                    continue
                if not ch.isspace():
                    p = c.beginPath()
                    glyph_set[gname].draw(_CanvasPen(glyph_set, p, scale, cx, PAGE_H - y))
                    c.drawPath(p, fill=1, stroke=0)
                cx += hmtx[gname][0] * scale
        c.showPage()
    c.save()
    return path


def hybrid(path: Path, pages: Sequence[Sequence[str]], image_lines: Sequence[str], *,
           image_box=(LEFT, 500.0, 480.0, 640.0), dpi: int = 200) -> Path:
    """Text layer plus a raster region (display-space box) that carries more text, like a letterhead."""
    x0, y0, x1, y1 = image_box
    w_pt, h_pt = x1 - x0, y1 - y0
    scale = dpi / 72.0
    img = Image.new("L", (int(w_pt * scale), int(h_pt * scale)), 255)
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(FONT_PATH, int(round(FONT_SIZE * scale)))
    for i, line in enumerate(image_lines):
        draw.text((6 * scale, (6 + i * LEADING) * scale), line, fill=0, font=font)
    c = _canvas(path)
    for n, lines in enumerate(pages):
        c.setFont("Helvetica", FONT_SIZE)
        for (x, y), line in zip(line_positions(len(lines)), lines):
            c.drawString(x, PAGE_H - y, line)
        if n == 0:
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            c.drawImage(ImageReader(buf), x0, PAGE_H - y1, width=w_pt, height=h_pt)
        c.showPage()
    c.save()
    return path


def blank(path: Path, n_pages: int = 1) -> Path:
    c = _canvas(path)
    for _ in range(n_pages):
        c.showPage()
    c.save()
    return path


FILLER = [
    "The synthetic claimant attended the clinic for a review of the injury.",
    "This paragraph exists only to create enough text for classifier tests.",
    "Nothing in these fixtures refers to a real person, place or organisation.",
    "Examination findings were recorded and compared with the earlier report.",
]


def filler_lines(n: int) -> list[str]:
    return [FILLER[i % len(FILLER)] for i in range(n)]
