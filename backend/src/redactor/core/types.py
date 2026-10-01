"""Normalized core types shared by extraction, detection and evaluation (plan §4.2).

Every text source is represented the same way: a list of words with boxes in displayed page
space (PDF points, origin top-left) plus a canonical page text built from those words (words
joined by one space, lines joined by one newline). Offsets always refer to that canonical text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence


@dataclass(frozen=True, slots=True, order=True)
class BBox:
    x0: float
    y0: float
    x1: float
    y1: float

    def __post_init__(self) -> None:
        if self.x1 < self.x0 or self.y1 < self.y0:
            raise ValueError("BBox must satisfy x0<=x1 and y0<=y1")

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def center(self) -> tuple[float, float]:
        return ((self.x0 + self.x1) / 2.0, (self.y0 + self.y1) / 2.0)

    def intersection_area(self, other: "BBox") -> float:
        w = min(self.x1, other.x1) - max(self.x0, other.x0)
        h = min(self.y1, other.y1) - max(self.y0, other.y0)
        return w * h if w > 0 and h > 0 else 0.0

    def overlap_fraction(self, other: "BBox") -> float:
        """Share of *this* box's area that lies inside `other` (0 for degenerate boxes)."""
        a = self.area
        return self.intersection_area(other) / a if a > 0 else 0.0

    def iou(self, other: "BBox") -> float:
        inter = self.intersection_area(other)
        union = self.area + other.area - inter
        return inter / union if union > 0 else 0.0

    def union(self, other: "BBox") -> "BBox":
        return BBox(min(self.x0, other.x0), min(self.y0, other.y0), max(self.x1, other.x1), max(self.y1, other.y1))

    def to_dict(self) -> dict[str, float]:
        return {"x0": self.x0, "y0": self.y0, "x1": self.x1, "y1": self.y1}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "BBox":
        return cls(float(d["x0"]), float(d["y0"]), float(d["x1"]), float(d["y1"]))

    @classmethod
    def union_all(cls, boxes: Iterable["BBox"]) -> "BBox":
        boxes = list(boxes)
        if not boxes:
            raise ValueError("union_all needs at least one box")
        out = boxes[0]
        for b in boxes[1:]:
            out = out.union(b)
        return out


@dataclass(frozen=True, slots=True)
class Word:
    index: int
    text: str
    bbox: BBox
    conf: float | None
    block: int
    line: int
    start: int
    end: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index, "text": self.text, "bbox": self.bbox.to_dict(), "conf": self.conf,
            "block": self.block, "line": self.line, "start": self.start, "end": self.end,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Word":
        return cls(int(d["index"]), d["text"], BBox.from_dict(d["bbox"]),
                   None if d.get("conf") is None else float(d["conf"]),
                   int(d["block"]), int(d["line"]), int(d["start"]), int(d["end"]))


@dataclass(frozen=True, slots=True)
class RawWord:
    """A word before offsets are assigned: text, box, confidence and its (block, line) group."""
    text: str
    bbox: BBox
    conf: float | None
    block: int
    line: int


def build_page_text(raw_words: Sequence[RawWord]) -> tuple[str, tuple[Word, ...]]:
    """Canonical page text: words in the given order, lines separated by newlines.

    Raw words must already be in reading order. A new line starts whenever (block, line)
    changes. Lines are re-numbered from 0 in order of appearance.
    """
    parts: list[str] = []
    words: list[Word] = []
    pos = 0
    prev_key: tuple[int, int] | None = None
    line_no = -1
    for rw in raw_words:
        if not rw.text or rw.text.isspace():
            continue
        text = rw.text.replace("\n", " ").replace("\r", " ").strip()
        if not text:
            continue
        key = (rw.block, rw.line)
        if key != prev_key:
            line_no += 1
            if parts:
                parts.append("\n")
                pos += 1
            prev_key = key
        elif parts:
            parts.append(" ")
            pos += 1
        start = pos
        parts.append(text)
        pos += len(text)
        words.append(Word(len(words), text, rw.bbox, rw.conf, rw.block, line_no, start, pos))
    return "".join(parts), tuple(words)


@dataclass(frozen=True)
class PageExtraction:
    document_id: str
    page: int
    text_source_id: str
    method: str  # text_layer | ocr | hybrid | gold_transcript
    width_pt: float
    height_pt: float
    words: tuple[Word, ...]
    text: str
    engine: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "redactor.page_extraction", "schema_version": "0.1.0",
            "document_id": self.document_id, "page": self.page, "text_source_id": self.text_source_id,
            "method": self.method, "width_pt": self.width_pt, "height_pt": self.height_pt,
            "words": [w.to_dict() for w in self.words], "text": self.text, "engine": dict(self.engine),
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "PageExtraction":
        return cls(d["document_id"], int(d["page"]), d["text_source_id"], d["method"],
                   float(d["width_pt"]), float(d["height_pt"]),
                   tuple(Word.from_dict(w) for w in d["words"]), d["text"], dict(d.get("engine", {})))

    def words_in_span(self, start: int, end: int) -> list[Word]:
        return [w for w in self.words if w.start < end and w.end > start]

    def mean_conf(self) -> float | None:
        confs = [w.conf for w in self.words if w.conf is not None]
        return sum(confs) / len(confs) if confs else None


@dataclass(frozen=True, slots=True)
class EntitySpan:
    document_id: str
    page: int | None
    field: str | None
    text_source_id: str
    start: int
    end: int
    text: str
    entity_type: str
    native_type: str
    confidence: float | None
    detector: str
    rule_id: str | None
    bboxes: tuple[BBox, ...] = ()
    attributes: tuple[tuple[str, str], ...] = ()
    group_id: str | None = None

    def sort_key(self) -> tuple:
        return (self.document_id, -1 if self.page is None else self.page, self.field or "",
                self.start, self.end, self.entity_type, self.detector, self.rule_id or "")

    def attr(self, key: str, default: str | None = None) -> str | None:
        for k, v in self.attributes:
            if k == key:
                return v
        return default

    def to_dict(self, *, include_text: bool = True) -> dict[str, Any]:
        d = {
            "document_id": self.document_id, "page": self.page, "field": self.field,
            "text_source_id": self.text_source_id, "start": self.start, "end": self.end,
            "entity_type": self.entity_type, "native_type": self.native_type,
            "confidence": None if self.confidence is None else round(self.confidence, 4),
            "detector": self.detector, "rule_id": self.rule_id,
            "bboxes": [b.to_dict() for b in self.bboxes],
            "attributes": {k: v for k, v in self.attributes}, "group_id": self.group_id,
        }
        if include_text:
            d["text"] = self.text
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "EntitySpan":
        return cls(d["document_id"], d.get("page"), d.get("field"), d["text_source_id"], int(d["start"]),
                   int(d["end"]), d.get("text", ""), d["entity_type"], d.get("native_type", d["entity_type"]),
                   d.get("confidence"), d["detector"], d.get("rule_id"),
                   tuple(BBox.from_dict(b) for b in d.get("bboxes", [])),
                   tuple(sorted((str(k), str(v)) for k, v in (d.get("attributes") or {}).items())),
                   d.get("group_id"))


def sort_spans(spans: Iterable[EntitySpan]) -> list[EntitySpan]:
    return sorted(spans, key=EntitySpan.sort_key)


@dataclass
class DocumentText:
    """What a detector sees: the pages of one document plus its document-level fields."""
    document_id: str
    pages: dict[int, PageExtraction]
    fields: dict[str, str] = field(default_factory=dict)

    def page_numbers(self) -> list[int]:
        return sorted(self.pages)

    def field_source_id(self, name: str) -> str:
        return f"field:{name}"
