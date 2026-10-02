"""Evaluate one split: gold projection, detection, protection, attribution and OCR sections.

Returns a public part (metrics and counts only) and a private part (mention-level records with text,
for runs/<id>/private/). Used by the experiment runner and by the synthetic tests.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping, Sequence

from ..core.types import DocumentText, EntitySpan
from ..taxonomy import Policy, Taxonomy
from . import attribution as attr
from .gold import GoldDocument, GoldMention, combine_status, gold_entities_summary, project_document
from .metrics import (DocEval, bootstrap_ci, detection_metrics, evaluate_document, protection_metrics,
                      stat_f1, stat_protection_recall)
from .ocr_metrics import mean_conf, page_text_metrics, summarize_recovery, surface_recovery


def evaluate_split(docs: Sequence[DocumentText], golds: Mapping[str, GoldDocument],
                   preds: Mapping[str, Sequence[EntitySpan]], *, page_classes: Mapping[str, Mapping[int, str]],
                   policy: Policy, taxonomy: Taxonomy, split: str,
                   transcripts: Mapping[str, Mapping[int, str]] | None = None,
                   bootstrap_rounds: int = 1000) -> tuple[dict[str, Any], dict[str, Any], list[GoldMention]]:
    evals: list[DocEval] = []
    all_mentions: list[GoldMention] = []
    attr_rows: list[dict[str, Any]] = []
    recovery: list[tuple[str, str]] = []
    private_mentions: list[dict[str, Any]] = []
    conf_by_class: dict[str, list[float]] = defaultdict(list)
    statuses = []
    missing_gold = []
    pages = 0
    for doc in sorted(docs, key=lambda d: d.document_id):
        pcs = page_classes.get(doc.document_id, {})
        pages += len(doc.pages)
        for p, pe in sorted(doc.pages.items()):
            conf_by_class[pcs.get(p, "unknown")] += [w.conf for w in pe.words if w.conf is not None]
        gold = golds.get(doc.document_id)
        if gold is None:
            missing_gold.append(doc.document_id)
            continue
        statuses.append(gold.status)
        mentions = project_document(gold, doc, policy=policy, taxonomy=taxonomy, page_classes=pcs)
        all_mentions += mentions
        ev = evaluate_document(doc.document_id, mentions, preds.get(doc.document_id, []), policy=policy, taxonomy=taxonomy,
                               page_classes=pcs, complete_pages=gold.pages_complete, complete_fields=gold.fields_complete,
                               ignore_regions=gold.ignore_regions, split=split)
        evals.append(ev)
        rows = attr.attribute(mentions, ev, doc)
        attr_rows += rows
        by_id = {r["id"]: r for r in rows}
        for m in mentions:
            if m.excluded is not None:
                continue
            got = attr.extracted_surface(m, doc)
            recovery.append((m.entity_type, surface_recovery(m.text, got)))
            st = ev.mention_status.get(m.mention_id, {})
            private_mentions.append({"id": m.mention_id, "document_id": m.document_id, "page": m.page, "field": m.field,
                                     "type": m.entity_type, "action": m.action, "text": m.text, "extracted": got,
                                     "start": m.start, "end": m.end, "detected": st.get("detected", False),
                                     "protected": st.get("protected"), "outcome": by_id.get(m.mention_id, {}).get("outcome")})
    public: dict[str, Any] = {
        "documents": sorted(d.document_id for d in docs), "pages": pages,
        "gold_status": combine_status(statuses) if statuses else "none",
        "documents_without_gold": sorted(missing_gold),
        "gold": gold_entities_summary(all_mentions),
        "detection": detection_metrics(evals, taxonomy),
        "protection": protection_metrics(evals),
        "bootstrap": {"overlap_any_f1": bootstrap_ci(evals, stat_f1("overlap_any"), n=bootstrap_rounds),
                      "strict_f1": bootstrap_ci(evals, stat_f1("strict"), n=bootstrap_rounds),
                      "protection_recall": bootstrap_ci(evals, stat_protection_recall(), n=bootstrap_rounds)},
        "attribution": attr.summarize(attr_rows),
        "counts": {k: sum(ev.counts.get(k, 0) for ev in evals) for k in sorted({k for ev in evals for k in ev.counts})},
        "ocr": {"mean_word_confidence": {k: mean_conf(v) for k, v in sorted(conf_by_class.items())},
                "entity_surface_recovery": summarize_recovery(recovery), "transcripts": _transcript_metrics(docs, transcripts)},
    }
    private = {"mentions": private_mentions, "attribution": attr_rows}
    return public, private, all_mentions


def _transcript_metrics(docs: Sequence[DocumentText], transcripts: Mapping[str, Mapping[int, str]] | None) -> dict[str, Any]:
    if not transcripts:
        return {"pages": 0, "note": "no gold transcripts yet (OCR subset is annotation-campaign work)"}
    rows = []
    for doc in docs:
        for page, ref in sorted((transcripts.get(doc.document_id) or {}).items()):
            pe = doc.pages.get(page)
            if pe is not None:
                rows.append(page_text_metrics(ref, pe.text))
    if not rows:
        return {"pages": 0}
    keys = ("cer", "cer_ci", "wer", "wer_ci", "bow_f1")
    return {"pages": len(rows), **{k: round(sum(r[k] for r in rows if r[k] is not None) / max(1, sum(1 for r in rows if r[k] is not None)), 4)
                                   for k in keys}}
