"""Candidates and overlap resolution (plan §7.1): checksum-validated > context rule > pattern >
lexicon/propagation; ties go to the longest, then earliest span, then type name."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

TIER_CHECKSUM = 4
TIER_CONTEXT = 3
TIER_PATTERN = 2
TIER_LEXICON = 1


@dataclass(frozen=True, order=True)
class Cand:
    start: int
    end: int
    etype: str
    rule_id: str
    tier: int
    confidence: float
    attrs: tuple[tuple[str, str], ...] = field(default=())

    def with_attrs(self, **kw: str) -> "Cand":
        merged = dict(self.attrs)
        merged.update({k: str(v) for k, v in kw.items() if v is not None})
        return Cand(self.start, self.end, self.etype, self.rule_id, self.tier, self.confidence,
                    tuple(sorted(merged.items())))


def resolve(cands: Iterable[Cand]) -> list[Cand]:
    ordered = sorted(set(cands), key=lambda c: (-c.tier, -(c.end - c.start), c.start, c.etype, c.rule_id))
    kept: list[Cand] = []
    for c in ordered:
        if c.end <= c.start:
            continue
        if any(c.start < k.end and k.start < c.end for k in kept):
            continue
        kept.append(c)
    return sorted(kept, key=lambda c: (c.start, c.end, c.etype, c.rule_id))
