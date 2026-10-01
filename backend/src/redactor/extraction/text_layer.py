"""Text-layer extraction with pdfium character boxes (plan §4.1 S1, §6)."""

from __future__ import annotations

from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

from ..core.canonical import sha256_obj
from ..core.types import BBox, PageExtraction, RawWord, build_page_text
from ..ingest.pdfinfo import PDFIUM_VERSION, page_geometry

CONFIG = {"extractor": "text_layer.pdfium", "version": "0.1.0", "word_gap_factor": 0.5, "min_gap_pt": 2.5}


def text_source_id() -> str:
    return f"text_layer.pdfium@{PDFIUM_VERSION}#{sha256_obj(CONFIG)[:8]}"


def raw_words(pdf_path: str | Path, page: int) -> tuple[list[RawWord], float, float]:
    """Words in pdfium's content order; a new line whenever pdfium emits a line break."""
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        p = pdf[page - 1]
        geom = page_geometry(p)
        w, h = geom.display_size
        tp = p.get_textpage()
        try:
            words: list[RawWord] = []
            chars: list[str] = []
            boxes: list[BBox] = []
            line = 0

            def flush() -> None:
                if chars:
                    real = [b for b in boxes if b.area > 0] or boxes
                    words.append(RawWord("".join(chars), BBox.union_all(real), None, 0, line))
                chars.clear()
                boxes.clear()

            for i in range(tp.count_chars()):
                code = pdfium_c.FPDFText_GetUnicode(tp.raw, i)
                ch = chr(code) if code else ""
                if ch in ("\r", "\n"):
                    flush()
                    if ch == "\n":
                        line += 1
                    continue
                if not ch or ch.isspace() or code in (0xFFFE, 0xFFFF):
                    flush()
                    continue
                left, bottom, right, top = tp.get_charbox(i, loose=False)
                box = geom.user_box_to_display(left, bottom, right, top)
                if boxes:
                    prev = boxes[-1]
                    gap = box.x0 - prev.x1
                    height = max(prev.height, box.height, 1.0)
                    same_line = abs(box.center[1] - prev.center[1]) < height
                    if not same_line or gap > max(CONFIG["min_gap_pt"], CONFIG["word_gap_factor"] * height):
                        flush()
                chars.append(ch)
                boxes.append(box)
            flush()
            return words, w, h
        finally:
            tp.close()
            p.close()
    finally:
        pdf.close()


def extract(document_id: str, pdf_path: str | Path, page: int) -> PageExtraction:
    words, w, h = raw_words(pdf_path, page)
    text, final = build_page_text(words)
    return PageExtraction(document_id, page, text_source_id(), "text_layer", round(w, 3), round(h, 3), final, text,
                          {"engine": "pdfium", "version": PDFIUM_VERSION, "config": CONFIG})
