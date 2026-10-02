"""One-to-one greedy span matching (plan §4.5), shared by detection metrics and IAA.

Schemes: strict (bounds + type), exact_boundary (bounds), overlap_typed (overlap + compatible type),
overlap_any (overlap). Candidate pairs are taken greedily by largest character overlap, then IoU, then
positions and identifiers, so the result is a total, input-order-independent function.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

SCHEMES = ("strict", "exact_boundary", "overlap_typed", "overlap_any")


@dataclass(frozen=True)
class MSpan:
    key: tuple            # (document_id, page or -1, field or "", text_source_id)
    start: int
    end: int
    entity_type: str
    ident: str            # stable identifier used only as the last tie-break

    @property
    def length(self) -> int:
        return self.end - self.start


def overlap(a: MSpan, b: MSpan) -> int:
    if a.key != b.key:
        return 0
    return max(0, min(a.end, b.end) - max(a.start, b.start))


def eligible(scheme: str, g: MSpan, p: MSpan, compatible: Callable[[str, str], bool]) -> bool:
    ov = overlap(g, p)
    if ov <= 0:
        return False
    if scheme == "strict":
        return g.start == p.start and g.end == p.end and g.entity_type == p.entity_type
    if scheme == "exact_boundary":
        return g.start == p.start and g.end == p.end
    if scheme == "overlap_typed":
        return compatible(g.entity_type, p.entity_type)
    if scheme == "overlap_any":
        return True
    raise ValueError(f"unknown scheme {scheme}")


def match(gold: Sequence[MSpan], pred: Sequence[MSpan], scheme: str,
          compatible: Callable[[str, str], bool] = lambda a, b: a == b) -> list[tuple[int, int]]:
    """Return (gold index, pred index) pairs, sorted by gold index."""
    by_key: dict[tuple, list[int]] = {}
    for j, p in enumerate(pred):
        by_key.setdefault(p.key, []).append(j)
    cands = []
    for i, g in enumerate(gold):
        for j in by_key.get(g.key, ()):
            p = pred[j]
            if not eligible(scheme, g, p, compatible):
                continue
            ov = overlap(g, p)
            union = g.length + p.length - ov
            cands.append((-ov, -round(ov / union, 6) if union else 0, g.start, g.end, p.start, p.end,
                          p.entity_type, g.ident, p.ident, i, j))
    cands.sort()
    used_g: set[int] = set()
    used_p: set[int] = set()
    pairs = []
    for c in cands:
        i, j = c[-2], c[-1]
        if i in used_g or j in used_p:
            continue
        used_g.add(i)
        used_p.add(j)
        pairs.append((i, j))
    return sorted(pairs)


def covered_chars(target: MSpan, spans: Sequence[MSpan]) -> int:
    """Characters of `target` covered by the union of `spans` on the same key."""
    iv = sorted((max(target.start, s.start), min(target.end, s.end)) for s in spans
                if s.key == target.key and s.start < target.end and target.start < s.end)
    total, cur_s, cur_e = 0, None, None
    for s, e in iv:
        if cur_e is None or s > cur_e:
            if cur_e is not None:
                total += cur_e - cur_s
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_e is not None:
        total += cur_e - cur_s
    return total
