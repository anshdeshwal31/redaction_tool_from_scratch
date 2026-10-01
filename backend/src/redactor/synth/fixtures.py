"""Synthetic fixture generator with exact ground truth (plan §11 F5, §12).

Each fixture is a PDF of one page class plus its truth as a `golden.annotation_set` (regions in
displayed page space, computed from the same geometry used to draw the text) and an entry in
`fixtures/synthetic/manifest.json`. Every name, number and place is invented. Builds are
byte-deterministic (reportlab invariant mode, fixed fonts, seeded noise).

Kinds: born_digital, scanned (noise + skew), vector_outlined, hybrid (text layer + raster letterhead)
and form (two columns; values drawn in a shuffled order, so the text order no longer follows the
visual label-value pairing — what the v1 layout rules exist for).
"""

from __future__ import annotations

import io
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth

from ..core.canonical import sha256_file, write_canonical
from ..taxonomy import load_policy, load_taxonomy
from . import pdfs
from .pdfs import FONT_PATH, FONT_SIZE, LEADING, LEFT, PAGE_H, PAGE_W, TOP

Seg = Any  # str | tuple[text, type, extra dict]


@dataclass(frozen=True)
class Mention:
    page: int
    text: str
    entity_type: str
    box: tuple[float, float, float, float]
    extra: dict


def _line(segs: Sequence[Seg]) -> tuple[str, list[tuple[int, int, str, dict]]]:
    text, spans = "", []
    for s in segs:
        if isinstance(s, str):
            text += s
        else:
            t, etype, extra = s
            spans.append((len(text), len(text) + len(t), etype, dict(extra)))
            text += t
    return text, spans


P = lambda t, cid, role, form="full": (t, "PERSON", {"cid": cid, "role": role, "a": {"name_form": form, "casing": "title"}})
D = lambda t, role="other": (t, "DATE", {"a": {"date_role": role}})
O = lambda t, cid, role: (t, "ORGANIZATION", {"cid": cid, "role": role})
X = lambda t, etype, a=None: (t, etype, {"a": a or {}})

LETTER = [
    ["Re: ", P("Wren Ashdale", "person_901", "plaintiff"), " DOB: ", X("14/02/1979", "DATE_OF_BIRTH", {"granularity": "full"})],
    ["Claim number: ", X("WC7654321", "CLAIM_NUMBER")],
    ["I examined Ms ", P("Ashdale", "person_901", "plaintiff", "title_surname"), " on ", D("3 March 2024", "examination"), "."],
    ["She lives at ", X("12 Fernhill Road Sampleton QLD 4999", "ADDRESS"), "."],
    ["Contact ", X("0491 570 156", "PHONE", {"number_class": "mobile"}), " or ", X("wren.ashdale@example.com", "EMAIL"), "."],
    ["Her Medicare number is ", X("2123 45670 1", "MEDICARE"), "."],
    ["Referred by Dr ", P("Tamsin Hollow", "person_902", "treating_practitioner"), " of ",
     O("Brookvale Medical Centre", "org_901", "medical_practice"), "."],
    ["Date of injury: ", D("20/06/2023", "date_of_injury"), "."],
]
FORM = [
    ("Surname:", P("Ashdale", "person_901", "plaintiff", "surname_only")),
    ("Given names:", P("Wren", "person_901", "plaintiff", "given_only")),
    ("Date of birth:", X("14/02/1979", "DATE_OF_BIRTH", {"granularity": "full"})),
    ("Phone:", X("0491 570 156", "PHONE", {"number_class": "mobile"})),
    ("Employer:", O("Brookvale Medical Centre", "org_901", "medical_practice")),
    ("Claim number:", X("WC7654321", "CLAIM_NUMBER")),
]
LETTERHEAD = [["Northgate ", O("Physio Group", "org_902", "medical_practice")], ["Phone ", X("0491 570 157", "PHONE", {"number_class": "mobile"})]]
VALUE_X = 300.0

_vera = None


def _vera_advances() -> tuple[dict[int, str], Any, float]:
    global _vera
    if _vera is None:
        f = TTFont(FONT_PATH)
        _vera = (f.getBestCmap(), f["hmtx"], FONT_SIZE / f["head"].unitsPerEm)
    return _vera


def vera_width(text: str) -> float:
    cmap, hmtx, scale = _vera_advances()
    w = 0.0
    for ch in text:
        g = cmap.get(ord(ch))
        w += FONT_SIZE * 0.5 if g is None else hmtx[g][0] * scale
    return w


def pil_width(text: str, dpi: int) -> float:
    scale = dpi / 72.0
    font = ImageFont.truetype(FONT_PATH, int(round(FONT_SIZE * scale)))
    return font.getlength(text) / scale


def _rotate_box(box, angle_deg: float):
    """Box after PIL's counter-clockwise rotation about the page centre (displayed space)."""
    if not angle_deg:
        return box
    cx, cy = PAGE_W / 2, PAGE_H / 2
    th = math.radians(angle_deg)
    pts = []
    for x, y in ((box[0], box[1]), (box[2], box[1]), (box[0], box[3]), (box[2], box[3])):
        dx, dy = x - cx, y - cy
        pts.append((cx + dx * math.cos(th) + dy * math.sin(th), cy - dx * math.sin(th) + dy * math.cos(th)))
    return (min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts))


def _mentions_for_lines(lines: Sequence[Sequence[Seg]], page: int, width_fn, *, x0: float = LEFT, y_top: float = TOP,
                        box_v=(-9.0, 3.0), skew: float = 0.0) -> tuple[list[str], list[Mention]]:
    texts, out = [], []
    for i, segs in enumerate(lines):
        text, spans = _line(segs)
        texts.append(text)
        y = y_top + i * LEADING
        for s, e, etype, extra in spans:
            bx0 = x0 + width_fn(text[:s])
            bx1 = x0 + width_fn(text[:e])
            box = (bx0 - 0.5, y + box_v[0], bx1 + 0.5, y + box_v[1])
            out.append(Mention(page, text[s:e], etype, _rotate_box(box, skew), extra))
    return texts, out


def _letter_lines() -> list[list[Seg]]:
    return [list(l) for l in LETTER]


def build_born_digital(path: Path) -> list[Mention]:
    texts, ms = _mentions_for_lines(_letter_lines(), 1, lambda t: stringWidth(t, "Helvetica", FONT_SIZE))
    pdfs.born_digital(path, [texts])
    return ms


def build_scanned(path: Path, *, dpi: int = 300, skew: float = 0.8, noise: float = 0.0015) -> list[Mention]:
    # PIL draws the text box from y - 0.8*size, so glyphs sit roughly between y-9 and y+3.5
    texts, ms = _mentions_for_lines(_letter_lines(), 1, lambda t: pil_width(t, dpi), box_v=(-9.5, 3.5), skew=skew)
    pdfs.scanned(path, [texts], dpi=dpi, skew_deg=skew, noise=noise, seed=11)
    return ms


def build_vector(path: Path) -> list[Mention]:
    texts, ms = _mentions_for_lines(_letter_lines(), 1, vera_width)
    pdfs.vector_outlined(path, [texts])
    return ms


def build_hybrid(path: Path, *, dpi: int = 300) -> list[Mention]:
    texts, ms = _mentions_for_lines(_letter_lines(), 1, lambda t: stringWidth(t, "Helvetica", FONT_SIZE))
    box = (LEFT, 500.0, 480.0, 560.0)
    img_texts, img_ms = _mentions_for_lines(LETTERHEAD, 1, lambda t: pil_width(t, dpi), x0=box[0] + 6,
                                            y_top=box[1] + 6 + FONT_SIZE * 0.8, box_v=(-9.5, 3.5))
    pdfs.hybrid(path, [texts], img_texts, image_box=box, dpi=dpi)
    return ms + img_ms


def build_form(path: Path, *, seed: int = 5) -> list[Mention]:
    """Labels drawn top to bottom, then the values in a shuffled order (content order != visual order)."""
    c = pdfs._canvas(path)
    c.setFont("Helvetica", FONT_SIZE)
    ms = []
    order = list(range(len(FORM)))
    random.Random(seed).shuffle(order)
    for i, (label, _) in enumerate(FORM):
        c.drawString(LEFT, PAGE_H - (TOP + i * 2 * LEADING), label)
    for i in order:
        value = FORM[i][1]
        t, etype, extra = value
        y = TOP + i * 2 * LEADING
        c.drawString(VALUE_X, PAGE_H - y, t)
        w = stringWidth(t, "Helvetica", FONT_SIZE)
        ms.append(Mention(1, t, etype, (VALUE_X - 0.5, y - 9.0, VALUE_X + w + 0.5, y + 3.0), dict(extra)))
    c.showPage()
    c.save()
    return sorted(ms, key=lambda m: (m.box[1], m.box[0]))


KINDS = {"syn_born_digital": ("born_digital", build_born_digital), "syn_scanned": ("scanned", build_scanned),
         "syn_vector": ("vector_outlined", build_vector), "syn_hybrid": ("hybrid", build_hybrid),
         "syn_form": ("born_digital", build_form)}


def truth_set(document_id: str, pdf_path: Path, mentions: Sequence[Mention]) -> dict[str, Any]:
    from ..dataset.models import AnnotationSet, Coverage, Entity, Provenance, Region
    policy, tax = load_policy(), load_taxonomy()
    ents = []
    for n, m in enumerate(sorted(mentions, key=lambda m: (m.page, round(m.box[1], 1), round(m.box[0], 1), m.entity_type)), 1):
        attrs = dict(m.extra.get("a") or {})
        role = m.extra.get("role")
        dec = policy.decide(m.entity_type, role, attrs, tax.validator(m.entity_type))
        cid = m.extra.get("cid")
        ents.append(Entity(entity_id=f"{document_id}.e{n:04d}", canonical_id=f"matter_syn/{cid}" if cid else None,
                           entity_type=m.entity_type, text=m.text, page=m.page,
                           regions=[Region(x0=round(m.box[0], 2), y0=round(m.box[1], 2), x1=round(m.box[2], 2), y1=round(m.box[3], 2))],
                           role=role, attributes=attrs, action=dec.action,
                           provenance=Provenance(annotator="adjudicated", pass_=1, origin="manual", adjudication="agreed")))
    ann = AnnotationSet(document_id=document_id, document_sha256=sha256_file(pdf_path), annotator="adjudicated", pass_=1,
                        mode="adjudicated", revision=1, guidelines_version="0.1.0", policy_ref=policy.ref,
                        coverage=Coverage(pages_complete=[1], types_complete=[]), entities=ents)
    return ann.dump()


def build_all(out_dir: Path) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"schema": "redactor.synthetic_fixtures", "schema_version": "0.1.0", "documents": []}
    for doc_id, (kind, fn) in sorted(KINDS.items()):
        pdf = out_dir / f"{doc_id}.pdf"
        mentions = fn(pdf)
        truth = out_dir / f"{doc_id}.truth.json"
        write_canonical(truth, truth_set(doc_id, pdf, mentions))
        manifest["documents"].append({"document_id": doc_id, "page_class": kind, "pdf_sha256": sha256_file(pdf),
                                      "truth_sha256": sha256_file(truth), "mentions": len(mentions)})
    write_canonical(out_dir / "manifest.json", manifest)
    return {"documents": len(manifest["documents"]), "mentions": sum(d["mentions"] for d in manifest["documents"])}
