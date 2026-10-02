"""Oracle text source (plan §4.3): verified gold transcripts as a `gold_transcript` text source.

Running a detector on it measures detection with perfect OCR. Word boxes are laid out inside each
transcribed line's box in proportion to the characters (transcripts carry line boxes only), so gold
projection by regions works on the oracle text as on any other source. Only verified transcripts count.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..core.canonical import read_json, sha256_obj
from ..core.types import BBox, DocumentText, PageExtraction, RawWord, build_page_text
from ..paths import golden_dir

METHOD = "gold_transcript"


def _line_words(text: str, box: BBox, line_no: int) -> list[RawWord]:
    words = text.split()
    total = sum(len(w) for w in words) + max(0, len(words) - 1)
    if not words or total == 0:
        return []
    out, pos = [], 0
    width = box.x1 - box.x0
    for w in words:
        x0 = box.x0 + width * pos / total
        x1 = box.x0 + width * (pos + len(w)) / total
        out.append(RawWord(w, BBox(round(x0, 3), box.y0, round(x1, 3), box.y1), 100.0, 0, line_no))
        pos += len(w) + 1
    return out


def oracle_page(document_id: str, page: int, transcript: Mapping[str, Any], width_pt: float, height_pt: float) -> PageExtraction:
    raw: list[RawWord] = []
    for i, line in enumerate(transcript["lines"]):
        b = line["bbox"]
        raw += _line_words(line["text"], BBox(float(b["x0"]), float(b["y0"]), float(b["x1"]), float(b["y1"])), i)
    text, words = build_page_text(raw)
    sid = f"{METHOD}#{sha256_obj(transcript['lines'])[:12]}"
    return PageExtraction(document_id, page, sid, METHOD, width_pt, height_pt, words, text, {"verified_by_set": True})


def oracle_documents(docs: Sequence[DocumentText]) -> dict[str, DocumentText]:
    """For each document, the pages that have a verified transcript, as an oracle DocumentText."""
    out: dict[str, DocumentText] = {}
    for d in docs:
        tdir = golden_dir() / "transcripts" / d.document_id
        pages = {}
        for p in sorted(tdir.glob("p*.json")) if tdir.exists() else []:
            t = read_json(p)
            if not t.get("verified_by"):
                continue
            ref = d.pages.get(int(t["page"]))
            if ref is None:
                continue
            pages[int(t["page"])] = oracle_page(d.document_id, int(t["page"]), t, ref.width_pt, ref.height_pt)
        if pages:
            out[d.document_id] = DocumentText(d.document_id, dict(sorted(pages.items())), {})
    return out


def restrict(doc: DocumentText, pages: Sequence[int]) -> DocumentText:
    """The same document limited to some pages and without fields (for like-for-like comparisons)."""
    return DocumentText(doc.document_id, {p: doc.pages[p] for p in sorted(pages) if p in doc.pages}, {})


def restrict_gold(gold, pages: Sequence[int]):
    """The same gold limited to some pages (page mentions only), for a like-for-like comparison."""
    from dataclasses import replace
    keep = set(pages)
    ann = gold.annotation.model_copy(update={
        "entities": [e for e in gold.annotation.entities if e.page in keep],
        "coverage": gold.annotation.coverage.model_copy(update={
            "pages_complete": sorted(p for p in gold.annotation.coverage.pages_complete if p in keep), "fields_complete": []})})
    return replace(gold, annotation=ann, ignore_regions={p: v for p, v in gold.ignore_regions.items() if p in keep})
