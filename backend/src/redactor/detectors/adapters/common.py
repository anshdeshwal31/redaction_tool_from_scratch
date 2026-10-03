"""Shared adapter machinery: label maps, span building with word boxes, overlap resolution, fingerprints.

An adapter implements `analyze(texts) -> list[list[Raw]]` (one result list per input text, offsets in
Python code points). Everything else (pages and document-level fields, label mapping, boxes, sorting,
fingerprint) is shared here, so the candidates differ only in what they detect.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from ...core.canonical import sha256_obj
from ...core.types import DocumentText, EntitySpan
from ...paths import config_dir


@dataclass(frozen=True)
class Raw:
    start: int
    end: int
    native: str
    score: float | None


def load_label_map(name: str) -> dict[str, Any]:
    p = config_dir() / "adapters" / f"{name}.labels.v0.1.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def utf16_to_cp(text: str, offsets: Sequence[int]) -> list[int]:
    """JavaScript (UTF-16 code unit) offsets -> Python code point offsets."""
    if all(ord(c) < 0x10000 for c in text):
        return list(offsets)
    table, u = {}, 0
    for i, c in enumerate(text):
        table[u] = i
        u += 2 if ord(c) >= 0x10000 else 1
    table[u] = len(text)
    return [table.get(o, min(len(text), o)) for o in offsets]


def resolve_overlaps(raws: Sequence[Raw]) -> list[Raw]:
    """Keep non-overlapping spans: higher score, then longer, then earlier, then label (total order)."""
    order = sorted(raws, key=lambda r: (-(r.score if r.score is not None else 0.0), -(r.end - r.start), r.start, r.native))
    kept: list[Raw] = []
    for r in order:
        if r.end <= r.start:
            continue
        if all(r.end <= k.start or r.start >= k.end for k in kept):
            kept.append(r)
    return sorted(kept, key=lambda r: (r.start, r.end, r.native))


class AdapterBase:
    name = "adapter"
    version = "0.1.0"
    label_map_name = ""

    def __init__(self, config: Mapping[str, Any] | None = None):
        self.config = dict(config or {})
        self.labels = load_label_map(self.label_map_name)
        self.map: dict[str, str] = {k: v for k, v in (self.labels.get("map") or {}).items()}
        self.ignore: set[str] = set(self.labels.get("ignore") or [])
        self.unmapped: dict[str, int] = {}
        self.dropped_overlaps = 0

    # -- to implement
    def engine_fingerprint(self) -> dict[str, Any]:
        raise NotImplementedError

    def analyze(self, texts: Sequence[str]) -> list[list[Raw]]:
        raise NotImplementedError

    # -- shared
    @property
    def tag(self) -> str:
        return f"{self.name}@{self.version}"

    def fingerprint(self) -> str:
        return f"{self.tag}#{sha256_obj({'config': self.config, 'labels': self.labels, 'engine': self.engine_fingerprint()})[:12]}"

    def _type(self, native: str) -> str | None:
        if native in self.ignore:
            return None
        t = self.map.get(native)
        if t is None:
            self.unmapped[native] = self.unmapped.get(native, 0) + 1
            return "OTHER"
        return t

    def detect(self, doc: DocumentText) -> list[EntitySpan]:
        from ...dataset.silver import regions_for_span
        units: list[tuple[int | None, str | None, str, str]] = []
        for p, pe in sorted(doc.pages.items()):
            units.append((p, None, pe.text_source_id, pe.text))
        for f, t in sorted(doc.fields.items()):
            units.append((None, f, "field", t))
        results = self.analyze([u[3] for u in units]) if units else []
        out: list[EntitySpan] = []
        for (page, field, source, text), raws in zip(units, results):
            kept = resolve_overlaps(raws)
            self.dropped_overlaps += len(raws) - len(kept)
            pe = doc.pages.get(page) if page is not None else None
            for r in kept:
                etype = self._type(r.native)
                if etype is None:
                    continue
                s, e = r.start, r.end
                while s < e and text[s].isspace():
                    s += 1
                while e > s and text[e - 1].isspace():
                    e -= 1
                if e <= s:
                    continue
                boxes = tuple(regions_for_span(pe, s, e)) if pe is not None else ()
                out.append(EntitySpan(doc.document_id, page, field, source, s, e, text[s:e], etype, r.native,
                                      None if r.score is None else round(float(r.score), 4), self.tag, f"{self.name}:{r.native}", boxes))
        return sorted(out, key=EntitySpan.sort_key)

    def stats(self) -> dict[str, Any]:
        return {"unmapped_labels": dict(sorted(self.unmapped.items())), "dropped_overlaps": self.dropped_overlaps}


def repo_path(*parts: str) -> Path:
    from ...paths import REPO_ROOT
    return REPO_ROOT.joinpath(*parts)
