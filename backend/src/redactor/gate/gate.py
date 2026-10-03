"""Release-gate orchestration for one matter (plan §4.11). Fail-closed, no force path.

Per page: immediate REVIEW triggers (unknown class, handwriting); for OCR pages a confidence check and,
if low, the bounded retry ladder (Tesseract settings -> rotation search -> second engine), re-running
the same checks after every attempt; a page that stays low goes to REVIEW. Detection runs on the chosen
text; detections from the reference attempt are projected onto it (union of attempts) and anything that
cannot be projected is boxed and sent to REVIEW. Then linking/policy/replacement, the final leak scan
(any hit -> BLOCKED), and the review items. A document is exportable only when no page is BLOCKED and
every item has a resolving decision.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Mapping, Sequence

from ..core.types import BBox, DocumentText, EntitySpan, PageExtraction
from ..detectors.base import run_detector
from ..detectors.combiner import build as build_detector
from ..evaluation.ocr_metrics import bag_of_words_f1
from ..extraction import attempts as att
from ..replacement.engine import MatterResult, pseudonymize
from ..replacement.vault import Vault
from ..taxonomy import Policy, Taxonomy
from . import leakscan, queue

AttemptFn = Callable[[str, int, Mapping[str, Any], PageExtraction], list[PageExtraction]]


@dataclass
class PageGate:
    document_id: str
    page: int
    page_class: str
    state: str = "PASS"
    reasons: list[str] = field(default_factory=list)
    chosen_source: str = ""
    attempts: list[dict[str, Any]] = field(default_factory=list)
    low_confidence: bool = False
    rescued_by: str | None = None


@dataclass
class GateResult:
    pages: list[PageGate]
    items: list[dict[str, Any]]
    hits: list[leakscan.Hit]
    matter: MatterResult
    chosen: dict[str, DocumentText]
    spans: dict[str, list[EntitySpan]]
    regions: list[dict[str, Any]]
    export_allowed: dict[str, bool]
    engine_comparison: list[dict[str, Any]]

    def digest(self) -> str:
        """Canonical digest of the gate outcome (states, attempts, items, hits, outputs): gate determinism."""
        from ..core.canonical import canonical_json, sha256_hex
        from ..replacement.engine import output_digest
        payload = {"pages": [{"d": p.document_id, "p": p.page, "state": p.state, "reasons": p.reasons, "source": p.chosen_source,
                              "attempts": p.attempts, "rescued_by": p.rescued_by} for p in self.pages],
                   "items": self.items, "hits": [h.to_public() for h in self.hits], "regions": self.regions,
                   "export": self.export_allowed, "outputs": output_digest(self.matter)}
        return sha256_hex(canonical_json(payload))

    def summary(self) -> dict[str, Any]:
        reasons: dict[str, int] = defaultdict(int)
        for p in self.pages:
            for r in p.reasons:
                reasons[r] += 1
        states: dict[str, int] = defaultdict(int)
        for p in self.pages:
            states[p.state] += 1
        low = [p for p in self.pages if p.low_confidence]
        return {"pages": len(self.pages), "states": dict(sorted(states.items())), "reasons": dict(sorted(reasons.items())),
                "items": len(self.items), "unresolved_items": sum(1 for i in self.items if not i["resolved"]),
                "leak_hits": len(self.hits), "leak_hits_by_method": dict(sorted(_count(h.method for h in self.hits).items())),
                "low_confidence_pages": len(low), "rescued": sum(1 for p in low if p.rescued_by),
                "rescued_by_rung": dict(sorted(_count(p.rescued_by for p in low if p.rescued_by).items())),
                "export_allowed": dict(sorted(self.export_allowed.items()))}


def _count(xs) -> dict:
    out: dict = defaultdict(int)
    for x in xs:
        out[x] += 1
    return out


def default_attempt(document_id: str, page: int, rung: Mapping[str, Any], ref: PageExtraction) -> list[PageExtraction]:
    kind = rung["kind"]
    if kind == "tesseract":
        return [att.tesseract_attempt(document_id, page, rung.get("settings") or {})]
    if kind == "rotation":
        return [att.rotation_attempt(document_id, page, int(r), ref.width_pt, ref.height_pt) for r in rung.get("rotations", [90, 270])]
    if kind == "rapidocr":
        return [att.rapid_attempt(document_id, page, ref.width_pt, ref.height_pt)]
    raise ValueError(f"unknown rung kind {kind}")


def span_boxes(pe: PageExtraction, start: int, end: int) -> list[BBox]:
    by_line: dict[int, list[BBox]] = defaultdict(list)
    for w in pe.words_in_span(start, end):
        by_line[w.line].append(w.bbox)
    return [BBox.union_all(b) for _, b in sorted(by_line.items())]


def project_span(s: EntitySpan, src: PageExtraction, dst: PageExtraction) -> EntitySpan | None:
    """Bounding-box projection (§4.3) of a span from one attempt onto another; None if nothing lands."""
    boxes = span_boxes(src, s.start, s.end)
    hits = [w for w in dst.words if any(w.bbox.overlap_fraction(b) >= 0.5 for b in boxes)]
    if not hits:
        return None
    a, b = min(w.start for w in hits), max(w.end for w in hits)
    return replace(s, text_source_id=dst.text_source_id, start=a, end=b, text=dst.text[a:b], bboxes=tuple(boxes),
                   rule_id=(s.rule_id or "") + "+projected")


def _choose_pages(ref_docs: Mapping[str, DocumentText], page_info, cfg, handwriting, attempt_fn):
    th = cfg["thresholds"]
    pages: list[PageGate] = []
    chosen: dict[str, DocumentText] = {}
    comparison = []
    for doc_id, doc in sorted(ref_docs.items()):
        new_pages = {}
        for p, pe in sorted(doc.pages.items()):
            info = (page_info.get(doc_id) or {}).get(p, {})
            pg = PageGate(doc_id, p, info.get("class", "unknown"), chosen_source=pe.text_source_id)
            if pg.page_class == "unknown":
                pg.reasons.append("unknown_page_class")
            if p in (handwriting or {}).get(doc_id, set()):
                pg.reasons.append("handwriting")
            best = pe
            ocr_page = pe.method in ("ocr", "hybrid") and info.get("method", pe.method) != "text_layer"
            if ocr_page:
                q = att.quality(pe, th["T_word"])
                pg.attempts.append({"rung": "reference", "source": pe.text_source_id, **q, "low": att.is_low(q, th)})
                pg.low_confidence = att.is_low(q, th)
                immediate = set(pg.reasons) & set(cfg.get("immediate_review", []))
                strict = cfg.get("second_engine") == "strict"
                if pg.low_confidence and not immediate:
                    best_q = q
                    for rung in cfg.get("retry_ladder", []):
                        rescued = False
                        for a in attempt_fn(doc_id, p, rung, pe):
                            qa = att.quality(a, th["T_word"])
                            low = att.is_low(qa, th)
                            pg.attempts.append({"rung": rung["name"], "source": a.text_source_id, **qa, "low": low})
                            if rung["kind"] == "rapidocr":
                                comparison.append({"document_id": doc_id, "page": p, "page_class": pg.page_class,
                                                   "bow_f1": bag_of_words_f1(best.text, a.text), "tesseract_mean": best_q["mean"],
                                                   "rapidocr_mean": qa["mean"]})
                            if (qa["mean"] or 0) > (best_q["mean"] or 0):
                                best, best_q = a, qa
                            if not low and not rescued:
                                pg.rescued_by = rung["name"]
                                best, best_q = a, qa
                                rescued = True
                        if rescued:
                            break
                    if not pg.rescued_by:
                        pg.reasons.append("low_confidence")
                    elif any(c["document_id"] == doc_id and c["page"] == p and (c["bow_f1"] or 0) < th["engine_agreement_bow_f1"]
                             for c in comparison) and pg.rescued_by != "second_engine":
                        pg.reasons.append("engine_disagreement")
                elif strict and not immediate:
                    for a in attempt_fn(doc_id, p, {"name": "second_engine", "kind": "rapidocr"}, pe):
                        f1 = bag_of_words_f1(pe.text, a.text)
                        comparison.append({"document_id": doc_id, "page": p, "page_class": pg.page_class, "bow_f1": f1,
                                           "tesseract_mean": q["mean"], "rapidocr_mean": att.quality(a, th["T_word"])["mean"]})
                        if (f1 or 0) < th["engine_agreement_bow_f1"]:
                            pg.reasons.append("engine_disagreement")
            pg.chosen_source = best.text_source_id
            new_pages[p] = best
            pages.append(pg)
        chosen[doc_id] = DocumentText(doc_id, new_pages, dict(doc.fields))
    return pages, chosen, comparison


def run_gate(ref_docs: Mapping[str, DocumentText], page_info: Mapping[str, Mapping[int, Mapping[str, Any]]], *,
             detector_cfg: Mapping[str, Any], policy: Policy, taxonomy: Taxonomy, gate_cfg: Mapping[str, Any], vault: Vault,
             decisions: Sequence[Mapping[str, Any]] = (), handwriting: Mapping[str, set[int]] | None = None,
             attempt_fn: AttemptFn = default_attempt) -> GateResult:
    pages, chosen, comparison = _choose_pages(ref_docs, page_info, gate_cfg, handwriting, attempt_fn)
    decided = queue.latest(decisions)
    items: list[dict[str, Any]] = []

    def add_item(doc_id, page, reason, start=None, end=None, field_=None, kind=None) -> dict[str, Any]:
        iid = queue.item_id(doc_id, page, reason, start, end, field_)
        it = {"item_id": iid, "document_id": doc_id, "page": page, "field": field_, "reason": reason, "start": start, "end": end,
              "kind": kind}
        it["decision"] = decided.get(iid, {}).get("decision")
        it["resolved"] = queue.resolves(it, decided.get(iid))
        items.append(it)
        return it

    # ---- detection on the chosen text, plus the reference attempt projected onto it (union of attempts)
    detector = build_detector(detector_cfg)
    spans = {d: list(v) for d, v in run_detector(detector, list(chosen.values())).items()}
    changed = {(d, p) for d, doc in chosen.items() for p, pe in doc.pages.items() if pe.text_source_id != ref_docs[d].pages[p].text_source_id}
    regions: list[dict[str, Any]] = []
    if changed:
        ref_spans = run_detector(build_detector(detector_cfg), [ref_docs[d] for d in sorted({d for d, _ in changed})])
        for d, lst in ref_spans.items():
            for s in lst:
                if s.page is None or (d, s.page) not in changed:
                    continue
                proj = project_span(s, ref_docs[d].pages[s.page], chosen[d].pages[s.page])
                if proj is None:
                    boxes = span_boxes(ref_docs[d].pages[s.page], s.start, s.end)
                    regions.append({"document_id": d, "page": s.page, "boxes": [b.to_dict() for b in boxes], "entity_type": s.entity_type})
                    add_item(d, s.page, "unprojectable_detection", s.start, s.end, kind=s.entity_type)
                elif not any(x.page == proj.page and x.start < proj.end and proj.start < x.end for x in spans[d]):
                    spans[d].append(proj)
    # ---- reviewer decisions are pipeline inputs
    for rec in decisions:
        if rec["decision"] == "add_span":
            pl = rec["payload"]
            d = pl["document_id"]
            pe = chosen[d].pages[int(pl["page"])]
            a, b = int(pl["start"]), int(pl["end"])
            spans[d].append(EntitySpan(d, int(pl["page"]), None, pe.text_source_id, a, b, pe.text[a:b], pl["entity_type"],
                                       pl["entity_type"], 1.0, "reviewer", rec["item_id"], tuple(span_boxes(pe, a, b)),
                                       (("override_action", pl.get("action", "SYNTHETIC")),)))
        elif rec["decision"] == "add_region":
            regions.append({**rec["payload"], "source": "reviewer"})
    for d, lst in spans.items():
        out = []
        for s in lst:
            dec = policy.decide(s.entity_type, s.attr("role"), {k: v for k, v in s.attributes}, taxonomy.validator(s.entity_type))
            iid = queue.item_id(d, s.page, "policy_review", s.start, s.end, s.field)
            choice = decided.get(iid, {}).get("decision")
            if dec.action == "REVIEW" and choice in ("confirm_protect", "not_pii"):
                s = replace(s, attributes=tuple(sorted(dict(s.attributes, override_action="SYNTHETIC" if choice == "confirm_protect" else "KEEP").items())))
            out.append(s)
        spans[d] = sorted(out, key=EntitySpan.sort_key)
    # ---- multi-detector disagreement: protect the union, send the page to REVIEW
    if len(detector_cfg.get("detectors", [])) > 1:
        per = [run_detector(build_detector({"detectors": [dc]}), list(chosen.values())) for dc in detector_cfg["detectors"]]
        tol = int(gate_cfg["thresholds"].get("boundary_tolerance_chars", 2))
        for d in chosen:
            for i in range(len(per)):
                for j in range(i + 1, len(per)):
                    for a in per[i].get(d, []):
                        for b in per[j].get(d, []):
                            if a.page == b.page and a.field == b.field and a.start < b.end and b.start < a.end and (
                                    not taxonomy.compatible_types(a.entity_type, b.entity_type)
                                    or abs(a.start - b.start) > tol or abs(a.end - b.end) > tol):
                                add_item(d, a.page, "detector_disagreement", min(a.start, b.start), max(a.end, b.end))
    # ---- replacement and its own review items
    matter = pseudonymize(list(chosen.values()), spans, policy=policy, taxonomy=taxonomy, vault=vault)
    for d, r in matter.documents.items():
        for rv in r.reviews:
            add_item(d, rv.get("page"), rv["reason"], rv.get("start"), rv.get("end"), rv.get("field"), rv.get("entity_type"))
    # ---- final leak scan after all retries
    hits = leakscan.scan(matter, vault, cfg=gate_cfg.get("final_leak_scan", {}), policy=policy, taxonomy=taxonomy,
                         detector=build_detector({"detectors": [{"name": gate_cfg.get("final_leak_scan", {}).get("detector_rerun", "baseline")}]}))
    for h in hits:
        add_item(h.document_id, h.page, f"leak_{h.method}", h.start, h.end, h.field, h.kind)
    # ---- page-level items and states
    for pg in pages:
        for r in pg.reasons:
            add_item(pg.document_id, pg.page, r)
    open_by_page: dict[tuple, list[dict]] = defaultdict(list)
    any_by_page: dict[tuple, int] = defaultdict(int)
    for it in items:
        any_by_page[(it["document_id"], it["page"])] += 1
        if not it["resolved"]:
            open_by_page[(it["document_id"], it["page"])].append(it)
    export: dict[str, bool] = {}
    for pg in pages:
        open_items = open_by_page.get((pg.document_id, pg.page), [])
        if any(i["reason"].startswith("leak_") for i in open_items):
            pg.state = "BLOCKED"
        elif open_items:
            pg.state = "REVIEW"
        elif any_by_page.get((pg.document_id, pg.page)):
            pg.state = "REVIEW_DECIDED"     # REVIEW with a recorded decision: exportable
        else:
            pg.state = "PASS"
    for d in chosen:
        doc_items_open = [i for i in items if i["document_id"] == d and not i["resolved"]]
        export[d] = not doc_items_open and all(p.state in ("PASS", "REVIEW_DECIDED") for p in pages if p.document_id == d)
    items.sort(key=lambda i: (i["document_id"], i["page"] or 0, i["reason"], i["start"] or 0))
    return GateResult(pages, items, hits, matter, chosen, spans, regions, export, comparison)
