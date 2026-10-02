"""Gold loading and projection onto a text source (plan §4.3).

A gold mention is projected onto the words of a text source X whose boxes lie at least 50 % inside
one of the mention's regions; the span runs from the first to the last such word (in X's text order).
When the mention's `text_anchor` is on X itself, its exact offsets are used instead. No word means the
mention was not recovered by extraction. Ties cannot arise: word order in X is a total order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..core.canonical import read_json
from ..core.types import BBox, DocumentText, PageExtraction
from ..dataset.models import AnnotationSet, Entity
from ..paths import golden_dir
from ..taxonomy import PROTECT_ACTIONS, Policy, Taxonomy

MIN_WORD_OVERLAP = 0.5
GOLD_STATUSES = ("verified", "partly_verified", "silver")


@dataclass(frozen=True)
class GoldMention:
    mention_id: str
    document_id: str
    page: int | None
    field: str | None
    text_source_id: str
    start: int | None
    end: int | None
    entity_type: str
    text: str | None
    action: str
    role: str | None
    canonical_id: str | None
    certainty: str
    critical: bool
    excluded: str | None          # None, or why the mention is not scored: uncertain | review | region_only | ignore_region | not_complete
    projected_by: str | None      # anchor | regions | None (not recovered)
    page_class: str | None
    attributes: tuple[tuple[str, str], ...] = ()
    flags: tuple[str, ...] = ()

    @property
    def key(self) -> tuple:
        return (self.document_id, -1 if self.page is None else self.page, self.field or "", self.text_source_id)

    @property
    def projected(self) -> bool:
        return self.start is not None

    @property
    def protect(self) -> bool:
        return self.action in PROTECT_ACTIONS

    def sort_key(self) -> tuple:
        return self.key + (self.start if self.start is not None else -1, self.end or -1, self.entity_type, self.mention_id)


@dataclass
class GoldDocument:
    document_id: str
    annotation: AnnotationSet
    status: str
    path: str
    ignore_regions: dict[int, list[BBox]] = field(default_factory=dict)

    @property
    def pages_complete(self) -> set[int]:
        return set(self.annotation.coverage.pages_complete)

    @property
    def fields_complete(self) -> set[str]:
        return set(self.annotation.coverage.fields_complete)


def gold_path(document_id: str, base: Path | None = None) -> tuple[Path, str] | None:
    """Adjudicated gold first, then the silver pass (labelled silver)."""
    root = base or golden_dir()
    for p, kind in ((root / "annotations" / f"{document_id}.ann.json", "gold"),
                    (root / "annotations_raw" / "claude_silver" / f"{document_id}.ann.json", "silver")):
        if p.exists():
            return p, kind
    return None


def load_gold(document_id: str, *, path: Path | None = None, base: Path | None = None) -> GoldDocument | None:
    if path is None:
        found = gold_path(document_id, base)
        if found is None:
            return None
        path, kind = found
    else:
        kind = "gold"
    ann = AnnotationSet.model_validate(read_json(path))
    if kind == "silver" or ann.annotator == "claude_silver":
        status = "silver"
    else:
        silver_left = any(e.provenance.origin == "silver" and e.provenance.adjudication != "verified_silver"
                          for e in ann.entities)
        status = "partly_verified" if silver_left else "verified"
    ignore: dict[int, list[BBox]] = {}
    for ig in ann.ignore_regions:
        ignore.setdefault(ig.page, []).extend(BBox(r.x0, r.y0, r.x1, r.y1) for r in ig.regions)
    return GoldDocument(document_id, ann, status, str(path), ignore)


def combine_status(statuses: Sequence[str]) -> str:
    s = set(statuses)
    if not s or s == {"verified"}:
        return "verified" if s else "silver"
    if s == {"silver"}:
        return "silver"
    return "partly_verified"


def _regions(e: Entity) -> list[BBox]:
    return [BBox(r.x0, r.y0, r.x1, r.y1) for r in e.regions]


def project_regions(regions: Sequence[BBox], pe: PageExtraction, min_overlap: float = MIN_WORD_OVERLAP) -> tuple[int, int] | None:
    hits = [w for w in pe.words if any(w.bbox.overlap_fraction(r) >= min_overlap for r in regions)]
    if not hits:
        return None
    return min(w.start for w in hits), max(w.end for w in hits)


EDGE_PUNCT = set(".,;:!?()[]{}\"'“”‘’<>|*")


def trim_edges(text: str, start: int, end: int, gold_text: str | None) -> tuple[int, int]:
    """Drop word-edge punctuation that the gold surface does not carry ("2024." -> "2024")."""
    g = gold_text or ""
    while end > start and text[end - 1] in EDGE_PUNCT and not g.endswith(text[end - 1]):
        end -= 1
    while start < end and text[start] in EDGE_PUNCT and not g.startswith(text[start]):
        start += 1
    return start, end


def project_entity(e: Entity, source_id: str, pe: PageExtraction | None) -> tuple[int | None, int | None, str | None]:
    if e.text_anchor is not None and e.text_anchor.text_source_id == source_id:
        return e.text_anchor.start, e.text_anchor.end, "anchor"
    if pe is not None and e.regions:
        span = project_regions(_regions(e), pe)
        if span:
            s, t = trim_edges(pe.text, span[0], span[1], e.text)
            if t > s:
                return s, t, "regions"
    return None, None, None


def _in_ignore(e: Entity, ignore: Mapping[int, list[BBox]]) -> bool:
    if e.page is None or e.page not in ignore:
        return False
    regs = _regions(e)
    return bool(regs) and all(any(r.overlap_fraction(ig) >= 0.5 for ig in ignore[e.page]) for r in regs)


def project_document(gold: GoldDocument, doc: DocumentText, *, policy: Policy, taxonomy: Taxonomy,
                     page_classes: Mapping[int, str] | None = None) -> list[GoldMention]:
    """Gold mentions of one document projected onto the text sources the detector saw."""
    page_classes = page_classes or {}
    out: list[GoldMention] = []
    complete_pages = gold.pages_complete
    complete_fields = gold.fields_complete
    for e in gold.annotation.entities:
        if e.page is not None:
            pe = doc.pages.get(e.page)
            source = pe.text_source_id if pe is not None else ""
        else:
            pe = None
            source = doc.field_source_id(e.field or "")
        start, end, how = project_entity(e, source, pe)
        if e.field is not None and e.field not in doc.fields:
            start = end = how = None
        excluded = None
        if taxonomy.region_only(e.entity_type):
            excluded = "region_only"
        elif e.certainty == "uncertain":
            excluded = "uncertain"
        elif e.action == "REVIEW":
            excluded = "review"
        elif (e.page is not None and e.page not in complete_pages) or (e.field is not None and e.field not in complete_fields):
            excluded = "not_complete"
        elif _in_ignore(e, gold.ignore_regions):
            excluded = "ignore_region"
        attrs = tuple(sorted((str(k), str(v)) for k, v in e.attributes.items()))
        out.append(GoldMention(
            mention_id=e.entity_id, document_id=gold.document_id, page=e.page, field=e.field,
            text_source_id=source, start=start, end=end, entity_type=e.entity_type, text=e.text,
            action=e.action, role=e.role, canonical_id=e.canonical_id, certainty=e.certainty,
            critical=policy.is_critical(e.entity_type, e.role), excluded=excluded, projected_by=how,
            page_class=page_classes.get(e.page) if e.page is not None else "field", attributes=attrs,
            flags=tuple(e.flags)))
    return sorted(out, key=GoldMention.sort_key)


def gold_entities_summary(mentions: Sequence[GoldMention]) -> dict[str, Any]:
    return {
        "mentions": len(mentions),
        "scored": sum(1 for m in mentions if m.excluded is None),
        "excluded": {k: sum(1 for m in mentions if m.excluded == k)
                     for k in sorted({m.excluded for m in mentions if m.excluded})},
        "not_projected": sum(1 for m in mentions if m.excluded is None and not m.projected),
    }
