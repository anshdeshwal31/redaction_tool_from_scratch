"""E8: release-gate metrics (plan §4.5 "Release gate").

- gate recall: share of pages with at least one missed gold_protect mention that the gate routes to
  REVIEW or BLOCKED (the most important one);
- unsafe passes: pages marked PASS that still leak (target 0);
- review load: share of pages in REVIEW, by reason;
- retry rescue rate: low-confidence pages rescued by a rung of the ladder;
- precision of the final leak-scan hits (a hit is true when it lies on a residual gold surface).
Plus the OCR comparison between the two engines (plan §11 G1).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping, Sequence

from ..gate.gate import GateResult
from ..taxonomy import PROTECT_ACTIONS
from .gold import GoldMention
from .metrics import ratio


def _cover(start: int, end: int, ivs) -> int:
    covered = 0
    pos = start
    for a, b in sorted(ivs):
        a, b = max(a, pos), min(b, end)
        if b > a:
            covered += b - a
            pos = b
    return covered


def gate_metrics(result: GateResult, gold: Mapping[str, Sequence[GoldMention]]) -> dict[str, Any]:
    edits = defaultdict(list)
    for d, r in result.matter.documents.items():
        for e in r.edits:
            if e.action in PROTECT_ACTIONS and e.page is not None:
                edits[(d, e.page)].append((e.start, e.end))
    missed_pages: set[tuple[str, int]] = set()
    residual_text: dict[str, list[str]] = defaultdict(list)
    any_text: dict[str, list[str]] = defaultdict(list)
    for d, ms in gold.items():
        for m in ms:
            if m.text:
                any_text[d].append(" ".join(m.text.split()).casefold())
            if m.excluded is not None or not m.protect or m.page is None:
                continue
            if not m.projected or _cover(m.start, m.end, edits[(d, m.page)]) < m.end - m.start:
                missed_pages.add((d, m.page))
                if m.text:
                    residual_text[d].append(" ".join(m.text.split()).casefold())
    state = {(p.document_id, p.page): p.state for p in result.pages}
    flagged = {k for k, s in state.items() if s in ("REVIEW", "BLOCKED")}
    unsafe = {k for k in missed_pages if state.get(k) in ("PASS", "REVIEW_DECIDED")}
    by_reason: dict[str, int] = defaultdict(int)
    for p in result.pages:
        if p.state in ("REVIEW", "BLOCKED"):
            for r in set(p.reasons) or {"span_items"}:
                by_reason[r] += 1
    n = len(result.pages)
    low = [p for p in result.pages if p.low_confidence]
    true_hits = 0
    false_by: dict[str, int] = defaultdict(int)
    for h in result.hits:
        r = result.matter.documents.get(h.document_id)
        text = (r.pages.get(h.page, "") if h.page is not None else r.fields.get(h.field or "", "")) if r else ""
        hit = " ".join(text[h.start:h.end].split()).casefold()
        if any(hit and (hit in g or g in hit) for g in residual_text.get(h.document_id, [])):
            true_hits += 1
            continue
        # false against silver: the surface is a gold mention somewhere (another occurrence the labels
        # missed, or a KEEP/excluded mention), or it is in no gold mention at all
        cls = "gold_surface_elsewhere" if any(hit and (hit in g or g in hit) for g in any_text.get(h.document_id, [])) else "not_in_gold"
        false_by[f"{h.method}:{cls}"] += 1
    comp = result.engine_comparison
    by_class: dict[str, list[float]] = defaultdict(list)
    for c in comp:
        if c["bow_f1"] is not None:
            by_class[c["page_class"]].append(c["bow_f1"])
    return {
        "pages": n, "pages_with_missed_protect": len(missed_pages),
        "gate_recall": ratio(len(missed_pages & flagged), len(missed_pages)),
        "unsafe_passes": len(unsafe),
        "review_load": ratio(sum(1 for p in result.pages if p.state == "REVIEW"), n),
        "blocked_pages": sum(1 for p in result.pages if p.state == "BLOCKED"),
        "review_by_reason": dict(sorted(by_reason.items())),
        "low_confidence_pages": len(low), "retry_rescue_rate": ratio(sum(1 for p in low if p.rescued_by), len(low)),
        "rescued_by_rung": dict(sorted(_count(p.rescued_by for p in low if p.rescued_by).items())),
        "leak_scan_hits": len(result.hits), "leak_scan_precision": ratio(true_hits, len(result.hits)),
        "leak_scan_false_by": dict(sorted(false_by.items())),
        "export_allowed": dict(sorted(result.export_allowed.items())),
        "engine_comparison": {"pages": len(comp), "mean_bow_f1_by_class": {k: round(sum(v) / len(v), 4) for k, v in sorted(by_class.items())},
                              "mean_conf": {"tesseract": _mean(c["tesseract_mean"] for c in comp),
                                            "rapidocr": _mean(c["rapidocr_mean"] for c in comp)}},
    }


def _mean(xs) -> float | None:
    v = [x for x in xs if x is not None]
    return round(sum(v) / len(v), 2) if v else None


def _count(xs) -> dict:
    out: dict = defaultdict(int)
    for x in xs:
        out[x] += 1
    return out
