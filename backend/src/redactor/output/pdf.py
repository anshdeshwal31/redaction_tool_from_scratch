"""PDF output: rasterise every page and burn opaque boxes into the pixels (plan §4.12.3).

- Every page (born-digital too) is rendered with the pinned pdfium at a fixed DPI, in grayscale, so no
  text layer, hidden text or vector text survives.
- Boxes cover every SYNTHETIC/REDACT span (word boxes per line, padded and grown to the line height) and
  every region (unprojectable detections, reviewer regions, signatures). A span without a box was
  already widened to its whole line by the JSON builder (fail-closed).
- No synthetic text is drawn into the image.
- The container comes from a minimal deterministic writer: one Flate-compressed image per page, no
  Info dictionary, no XMP, no timestamps, the document ID derived from the doc_id and page hashes.
- Verification: the PDF must have no font, no text operator, no metadata and no extractable text; each
  burned page is re-OCRed locally and run through the final leak scan (any hit blocks the export).
"""

from __future__ import annotations

import hashlib
import io
import re
import zlib
from typing import Any, Callable, Mapping, Sequence

from PIL import Image, ImageDraw

from ..core.coords import points_box_to_pixels
from ..core.types import BBox

PROTECT = ("SYNTHETIC", "REDACT")
PAD_PT = 1.5
LINE_GROW = 0.15        # share of the box height added above and below (snaps to the line's ink)
ZLEVEL = 6


def page_boxes(page: Mapping[str, Any], regions: Sequence[Mapping[str, Any]]) -> list[BBox]:
    boxes = [BBox.from_dict(b) for s in page["spans"] if s["action"] in PROTECT for b in s["bboxes"]]
    for r in regions:
        boxes += [BBox.from_dict(b) for b in r.get("boxes", [])]
        if r.get("box"):
            boxes.append(BBox.from_dict(r["box"]))
    return sorted(set(boxes), key=lambda b: (b.y0, b.x0, b.y1, b.x1))


def burn(img: Image.Image, boxes: Sequence[BBox], dpi: int) -> tuple[Image.Image, list[list[int]]]:
    out = img.convert("L").copy()
    draw = ImageDraw.Draw(out)
    drawn: list[list[int]] = []
    for b in boxes:
        g = b.height * LINE_GROW
        grown = BBox(max(0.0, b.x0 - PAD_PT), max(0.0, b.y0 - PAD_PT - g), b.x1 + PAD_PT, b.y1 + PAD_PT + g)
        l, t, r, bt = points_box_to_pixels(grown, dpi)
        r, bt = min(r, out.width), min(bt, out.height)
        if r > l and bt > t:
            draw.rectangle((l, t, r - 1, bt - 1), fill=0)
            drawn.append([l, t, r, bt])
    return out, drawn


def write_pdf(pages: Sequence[Image.Image], dpi: int, doc_id: str) -> bytes:
    """Deterministic minimal PDF from grayscale page images."""
    objs: list[bytes] = []

    def add(b: bytes) -> int:
        objs.append(b)
        return len(objs)

    add(b"")   # 1: catalog (filled below)
    add(b"")   # 2: pages
    kids = []
    hashes = []
    for img in pages:
        g = img.convert("L")
        raw = g.tobytes()
        hashes.append(hashlib.sha256(raw).hexdigest())
        data = zlib.compress(raw, ZLEVEL)
        im = add(b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceGray /BitsPerComponent 8 "
                 b"/Filter /FlateDecode /Length %d >>\nstream\n" % (g.width, g.height, len(data)) + data + b"\nendstream")
        w_pt, h_pt = round(g.width * 72.0 / dpi, 3), round(g.height * 72.0 / dpi, 3)
        content = b"q %s 0 0 %s 0 0 cm /Im0 Do Q" % (_num(w_pt), _num(h_pt))
        c = add(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
        kids.append(add(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %s %s] /Resources << /XObject << /Im0 %d 0 R >> >> "
                        b"/Contents %d 0 R >>" % (_num(w_pt), _num(h_pt), im, c)))
    objs[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objs[1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (b" ".join(b"%d 0 R" % k for k in kids), len(kids))
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % i + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
    for off in offsets:
        out.write(b"%010d 00000 n \n" % off)
    fid = hashlib.sha256(("\x1f".join([doc_id, *hashes])).encode()).hexdigest()[:32].encode()
    out.write(b"trailer\n<< /Size %d /Root 1 0 R /ID [<%s> <%s>] >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, fid, fid, xref))
    return out.getvalue()


def _num(x: float) -> bytes:
    s = f"{x:.3f}".rstrip("0").rstrip(".")
    return s.encode()


def verify_container(pdf: bytes, doc_id: str) -> dict[str, Any]:
    """Structural checks: no fonts, text operators, Info or XMP metadata, no extractable text."""
    import pypdfium2 as pdfium
    checks = {
        "no_font": b"/Font" not in pdf,
        "no_info": b"/Info" not in pdf,
        "no_xmp": b"/Metadata" not in pdf and b"x:xmpmeta" not in pdf,
        "no_text_operators": re.search(rb"\bBT\b[\s\S]*?\bET\b", _content_streams(pdf)) is None,
    }
    doc = pdfium.PdfDocument(pdf)
    try:
        chars = 0
        for i in range(len(doc)):
            tp = doc[i].get_textpage()
            chars += tp.count_chars()
            tp.close()
        checks["no_text_layer"] = chars == 0
        checks["pages"] = len(doc)
    finally:
        doc.close()
    checks["ok"] = all(v for k, v in checks.items() if k != "pages")
    return checks


def _content_streams(pdf: bytes) -> bytes:
    return b"\n".join(m.group(1) for m in re.finditer(rb"<< /Length \d+ >>\nstream\n(.*?)\nendstream", pdf, re.S))


OcrFn = Callable[[Sequence[Image.Image], int], list[str]]


def burn_document(source_pdf, doc: Mapping[str, Any], regions: Sequence[Mapping[str, Any]], *, dpi: int = 300,
                  render=None) -> tuple[bytes, list[Image.Image], dict[str, Any]]:
    """Returns (pdf bytes, burned page images, local box manifest)."""
    from ..extraction.render import render_page
    render = render or render_page
    images, manifest = [], []
    for page in sorted(doc["pages"], key=lambda p: p["page"]):
        p = page["page"]
        boxes = page_boxes(page, [r for r in regions if r.get("page") == p])
        img, drawn = burn(render(source_pdf, p, dpi), boxes, dpi)
        images.append(img)
        manifest.append({"page": p, "boxes_pt": [b.to_dict() for b in boxes], "boxes_px": drawn})
    pdf = write_pdf(images, dpi, doc["document_id"])
    return pdf, images, {"document_id": doc["document_id"], "dpi": dpi, "pages": manifest}


def tesseract_ocr(images: Sequence[Image.Image], dpi: int) -> list[str]:
    """Local re-OCR of burned pages (pinned Tesseract container, no network); texts stay in memory."""
    import tempfile
    from pathlib import Path
    from ..core.types import build_page_text
    from ..extraction.base import OcrSettings
    from ..extraction.ocr_tesseract import TesseractConfig, parse_tsv, run_batch
    from ..extraction.render import png_bytes
    from ..paths import data_dir
    cfg = TesseractConfig.load()
    tmp_root = data_dir() / "tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=tmp_root) as td:
        ind, outd = Path(td) / "in", Path(td) / "out"
        ind.mkdir()
        pngs = []
        for i, img in enumerate(images, 1):
            pth = ind / f"p{i:04d}.png"
            pth.write_bytes(png_bytes(img))
            pngs.append(pth)
        tsvs = run_batch(cfg, pngs, outd, OcrSettings(dpi=dpi, psm=3))
        return [build_page_text(parse_tsv(tsvs[p].read_text(encoding="utf-8"), dpi))[0] for p in pngs]


def area_metrics(images_boxes: Sequence[Sequence[BBox]], gold_regions: Sequence[Sequence[BBox]]) -> dict[str, float | None]:
    """Gold-protect region area covered by boxes, and collateral (boxed area outside gold) share,
    on a 1-pt grid per page (exact enough, deterministic)."""
    import numpy as np
    cov = tot = boxed = coll = 0
    for boxes, gold in zip(images_boxes, gold_regions):
        if not boxes and not gold:
            continue
        w = int(max([b.x1 for b in [*boxes, *gold]] + [1])) + 2
        h = int(max([b.y1 for b in [*boxes, *gold]] + [1])) + 2
        bm = np.zeros((h, w), dtype=bool)
        gm = np.zeros((h, w), dtype=bool)
        for b in boxes:
            g = b.height * LINE_GROW
            bm[max(0, int(b.y0 - PAD_PT - g)):int(b.y1 + PAD_PT + g) + 1, max(0, int(b.x0 - PAD_PT)):int(b.x1 + PAD_PT) + 1] = True
        for b in gold:
            gm[int(b.y0):max(int(b.y0) + 1, int(b.y1)), int(b.x0):max(int(b.x0) + 1, int(b.x1))] = True
        tot += int(gm.sum())
        cov += int((gm & bm).sum())
        boxed += int(bm.sum())
        coll += int((bm & ~gm).sum())
    return {"gold_area_covered": round(cov / tot, 4) if tot else None, "collateral_share": round(coll / boxed, 4) if boxed else None,
            "gold_area_pt2": tot, "boxed_area_pt2": boxed}
