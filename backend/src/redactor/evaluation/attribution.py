"""Attributing each gold_protect miss to OCR or detection (plan §4.6).

Outcome per gold_protect mention and experiment:
  OK                 detected and fully protected
  DETECTOR_MISS      the text source holds the surface exactly, the detector did not protect it
  OCR_INDUCED_MISS   the surface is degraded in the text source and the detector missed it
                     (with an oracle run: the detector does protect it on the oracle text)
  OCR_LOSS           the surface is not in the text source at all
  POLICY_MISS        detected, but the resolved action does not protect it
  REPLACEMENT_MISS   protected, but the surface is still in the output (needs the R1 output)
Every failed mention also carries `gate_flagged` (from the release gate, G1; false without a gate).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Mapping, Sequence

from ..core.types import DocumentText
from .gold import GoldMention
from .metrics import DocEval
from .ocr_metrics import surface_recovery

OUTCOMES = ("OK", "DETECTOR_MISS", "OCR_INDUCED_MISS", "OCR_LOSS", "POLICY_MISS", "REPLACEMENT_MISS")


def extracted_surface(g: GoldMention, doc: DocumentText) -> str | None:
    if not g.projected:
        return None
    if g.page is not None:
        pe = doc.pages.get(g.page)
        return pe.text[g.start:g.end] if pe else None
    return doc.fields.get(g.field or "", "")[g.start:g.end]


def attribute(gold: Sequence[GoldMention], ev: DocEval, doc: DocumentText, *,
              oracle_protected: Mapping[str, bool] | None = None,
              residual_ids: set[str] | None = None, gate_flagged: Mapping[str, bool] | None = None) -> list[dict[str, Any]]:
    out = []
    for g in gold:
        if g.excluded is not None or not g.protect:
            continue
        st = ev.mention_status.get(g.mention_id, {})
        recovery = surface_recovery(g.text, extracted_surface(g, doc))
        if recovery == "missing":
            outcome = "OCR_LOSS"
        elif st.get("protected"):
            outcome = "REPLACEMENT_MISS" if residual_ids and g.mention_id in residual_ids else "OK"
        elif st.get("covered_any_action") or st.get("detected"):
            # found by the detector, but the resolved action does not protect all of it
            outcome = "POLICY_MISS" if st.get("covered_any_action") else ("OCR_INDUCED_MISS" if recovery != "exact" else "DETECTOR_MISS")
        elif recovery == "exact":
            outcome = "DETECTOR_MISS"
        else:
            if oracle_protected is not None and g.mention_id in oracle_protected and not oracle_protected[g.mention_id]:
                outcome = "DETECTOR_MISS"   # missed on perfect text too: not OCR's fault
            else:
                outcome = "OCR_INDUCED_MISS"
        out.append({"id": g.mention_id, "document_id": g.document_id, "type": g.entity_type, "page_class": g.page_class,
                    "critical": g.critical, "recovery": recovery, "outcome": outcome,
                    "gate_flagged": bool(gate_flagged.get(g.mention_id)) if gate_flagged else False})
    return out


def summarize(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    by_pc: dict[str, Counter] = defaultdict(Counter)
    by_type: dict[str, Counter] = defaultdict(Counter)
    total: Counter = Counter()
    for r in rows:
        total[r["outcome"]] += 1
        by_pc[r["page_class"] or "unknown"][r["outcome"]] += 1
        by_type[r["type"]][r["outcome"]] += 1
    fmt = (lambda c: {k: c.get(k, 0) for k in OUTCOMES})
    return {"mentions": len(rows), "all": fmt(total),
            "by_page_class": {k: fmt(v) for k, v in sorted(by_pc.items())},
            "by_type": {k: fmt(v) for k, v in sorted(by_type.items())},
            "gate_flagged_failures": sum(1 for r in rows if r["outcome"] != "OK" and r["gate_flagged"]),
            "critical_failures": sum(1 for r in rows if r["outcome"] != "OK" and r["critical"])}
