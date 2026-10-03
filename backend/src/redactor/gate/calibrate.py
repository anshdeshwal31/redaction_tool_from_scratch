"""Provisional gate-threshold calibration on SILVER labels of the dev split (plan §4.11).

For every OCR page of the dev split: mean word confidence, share of low words, and whether the page holds
a gold_protect mention whose surface the OCR lost or degraded. A sweep over (T_mean, T_frac) reports how
many OCR-damaged pages each setting flags (recall) and how many pages it sends to the ladder (load).
Aggregates only. The chosen values are written to config/release_gate.v0.1.yaml by hand, labelled
provisional, and re-calibrated on verified gold before the test split is scored.
"""

from __future__ import annotations

from typing import Any

from ..evaluation.gold import load_gold, project_document
from ..evaluation.ocr_metrics import surface_recovery
from ..experiments.runner import dataset_documents, load_document_text, page_classes_for
from ..extraction.attempts import quality
from ..taxonomy import load_policy, load_taxonomy


def calibrate(t_word: float = 60.0) -> dict[str, Any]:
    policy, tax = load_policy(), load_taxonomy()
    rows = []
    for doc_id, split in dataset_documents(["dev"]):
        doc = load_document_text(doc_id)
        gold = load_gold(doc_id)
        mentions = project_document(gold, doc, policy=policy, taxonomy=tax) if gold else []
        pcs = page_classes_for(doc_id)
        for p, pe in sorted(doc.pages.items()):
            if pe.method not in ("ocr", "hybrid"):
                continue
            q = quality(pe, t_word)
            damaged = False
            for m in mentions:
                if m.page != p or m.excluded is not None or not m.protect:
                    continue
                got = pe.text[m.start:m.end] if m.projected else None
                if surface_recovery(m.text, got) in ("degraded", "missing"):
                    damaged = True
                    break
            rows.append({"class": pcs.get(p, "unknown"), "mean": q["mean"], "low_frac": q["low_frac"], "damaged": damaged})
    damaged_n = sum(r["damaged"] for r in rows)
    sweep = []
    for t_mean in (70.0, 75.0, 80.0, 85.0, 90.0):
        for t_frac in (0.10, 0.15, 0.20, 0.30):
            flagged = [r for r in rows if r["mean"] is not None and (r["mean"] < t_mean or r["low_frac"] > t_frac)]
            hit = sum(r["damaged"] for r in flagged)
            sweep.append({"T_mean": t_mean, "T_frac": t_frac, "flagged": len(flagged), "damaged_flagged": hit,
                          "recall": round(hit / damaged_n, 4) if damaged_n else None, "load": round(len(flagged) / len(rows), 4) if rows else None})
    return {"labels": "silver", "split": "dev", "ocr_pages": len(rows), "damaged_pages": damaged_n, "T_word": t_word, "sweep": sweep}
