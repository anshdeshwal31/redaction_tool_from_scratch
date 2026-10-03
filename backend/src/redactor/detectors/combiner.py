"""Combining detectors (plan §4.4 combiner; §11 C2 options).

Strategies:
- union             every span from every detector (overlaps kept)
- union_resolved    union, then overlaps resolved by the configured order
- intersection      spans that every detector found (overlap + compatible type); boxes from the first
- majority          spans found (by overlap) by more than half of the detectors
Overlap resolution order keys: priority (detector order), type_priority (per-type detector order),
longest, earliest, type_name.

C2 options (combiner YAML):
- only_types: {detector: [TYPE, ...]}   a detector contributes only these types (e.g. names,
  organisations and locations from an NER model next to the rule-based baseline)
- type_priority: {TYPE: [detector, ...]} for overlap conflicts on that type, the listed order wins
  (used by the `type_priority` order key; unlisted detectors come after, in detector order)
- min_confidence: {detector: float}      drop a detector's spans below this confidence
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping, Sequence

from ..core.canonical import sha256_obj
from ..core.types import DocumentText, EntitySpan, sort_spans
from ..taxonomy import load_taxonomy
from .base import Detector, create

STRATEGIES = ("union", "union_resolved", "intersection", "majority")
ORDER_KEYS = ("priority", "type_priority", "longest", "earliest", "type_name")


def _overlap(a: EntitySpan, b: EntitySpan) -> bool:
    return (a.page, a.field, a.text_source_id) == (b.page, b.field, b.text_source_id) and a.start < b.end and b.start < a.end


def resolve_overlaps(spans: Sequence[EntitySpan], order: Sequence[str], priority: Mapping[str, int],
                     type_priority: Mapping[str, Sequence[str]] | None = None) -> list[EntitySpan]:
    tp = type_priority or {}

    def key(s: EntitySpan):
        k = []
        for o in order:
            if o == "priority":
                k.append(priority.get(s.detector, 999))
            elif o == "type_priority":
                names = list(tp.get(s.entity_type, []))
                base = s.detector.split("@")[0]
                k.append(names.index(base) if base in names else len(names) + priority.get(s.detector, 999))
            elif o == "longest":
                k.append(-(s.end - s.start))
            elif o == "earliest":
                k.append(s.start)
            elif o == "type_name":
                k.append(s.entity_type)
        return tuple(k) + s.sort_key()
    kept: list[EntitySpan] = []
    for s in sorted(spans, key=key):
        if not any(_overlap(s, k) for k in kept):
            kept.append(s)
    return sort_spans(kept)


class Combined:
    def __init__(self, detectors: Sequence[Detector], strategy: str = "union_resolved",
                 overlap_resolution: Sequence[str] = ("priority", "longest", "earliest", "type_name"),
                 *, only_types: Mapping[str, Sequence[str]] | None = None, type_priority: Mapping[str, Sequence[str]] | None = None,
                 min_confidence: Mapping[str, float] | None = None):
        if strategy not in STRATEGIES:
            raise ValueError(f"unknown combiner strategy {strategy}")
        bad = [o for o in overlap_resolution if o not in ORDER_KEYS]
        if bad:
            raise ValueError(f"unknown overlap_resolution keys {bad}")
        self.detectors = list(detectors)
        self.strategy = strategy
        self.order = list(overlap_resolution)
        self.only_types = {k: sorted(v) for k, v in sorted((only_types or {}).items())}
        self.type_priority = {k: list(v) for k, v in sorted((type_priority or {}).items())}
        self.min_confidence = {k: float(v) for k, v in sorted((min_confidence or {}).items())}
        self.name = "combined[" + "+".join(d.name for d in self.detectors) + "]"
        self.version = "0.1.0"

    def fingerprint(self) -> str:
        opts = {"only_types": self.only_types, "type_priority": self.type_priority, "min_confidence": self.min_confidence}
        payload = {"strategy": self.strategy, "order": self.order, "detectors": [d.fingerprint() for d in self.detectors]}
        if any(opts.values()):
            payload["options"] = opts          # absent when unused, so earlier fingerprints are unchanged
        return sha256_obj(payload)[:16]

    def _filter(self, d: Detector, spans: list[EntitySpan]) -> list[EntitySpan]:
        keep = self.only_types.get(d.name)
        floor = self.min_confidence.get(d.name)
        return [s for s in spans if (keep is None or s.entity_type in keep)
                and (floor is None or (s.confidence if s.confidence is not None else 1.0) >= floor)]

    def prepare(self, docs):
        for d in self.detectors:
            if hasattr(d, "prepare"):
                d.prepare(docs)

    def detect(self, doc: DocumentText) -> list[EntitySpan]:
        outputs = [sort_spans(self._filter(d, d.detect(doc))) for d in self.detectors]
        allspans = [s for out in outputs for s in out]
        priority = {d.name: i for i, d in enumerate(self.detectors)}
        for d in self.detectors:
            priority.setdefault(f"{d.name}@{d.version}", priority[d.name])
        pri = {s.detector: priority.get(s.detector, priority.get(s.detector.split("@")[0], 999)) for s in allspans}
        if self.strategy == "union":
            return sort_spans(allspans)
        if self.strategy == "union_resolved":
            return resolve_overlaps(allspans, self.order, pri, self.type_priority)
        tax = load_taxonomy()
        need = len(outputs) if self.strategy == "intersection" else len(outputs) // 2 + 1
        base = resolve_overlaps(allspans, self.order, pri, self.type_priority)
        kept = []
        for s in base:
            votes = sum(1 for out in outputs if any(_overlap(s, o) and tax.compatible_types(s.entity_type, o.entity_type) for o in out))
            if votes >= need:
                kept.append(replace(s, confidence=round(votes / len(outputs), 4)))
        return sort_spans(kept)


def build(config: Mapping[str, Any]) -> Detector:
    """Experiment YAML `detectors:` + `combiner:` -> one detector."""
    dets = [create(d["name"], d.get("config") or {}) for d in config["detectors"]]
    if len(dets) == 1 and not config.get("combiner"):
        return dets[0]
    comb = config.get("combiner") or {}
    return Combined(dets, comb.get("strategy", "union_resolved"),
                    comb.get("overlap_resolution", ("priority", "longest", "earliest", "type_name")),
                    only_types=comb.get("only_types"), type_priority=comb.get("type_priority"),
                    min_confidence=comb.get("min_confidence"))
