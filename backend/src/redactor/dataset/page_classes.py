"""Human page-class verification (plan §9.2 P1) and its metric (E0, plan §4.5 "Page classification").

A person confirms or corrects the classifier's class for every page. Verifications are stored apart from
the metadata (`golden_dataset/page_classes/<doc>.json`), so re-running the classifier never erases them,
and E0 compares the classifier's class with the verified one. Once a page is verified, breakdowns by page
class use the verified class. Files hold classes, initials and dates only.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, get_args

from ..core.canonical import read_json, write_canonical
from ..paths import golden_dir
from .models import ContentKind

KINDS = tuple(get_args(ContentKind))


class PageClassError(ValueError):
    pass


def path_for(document_id: str) -> Path:
    return golden_dir() / "page_classes" / f"{document_id}.json"


def load(document_id: str) -> dict[int, dict[str, Any]]:
    p = path_for(document_id)
    return {int(k): v for k, v in read_json(p)["pages"].items()} if p.exists() else {}


def verified_classes(document_id: str) -> dict[int, str]:
    return {p: v["content_kind"] for p, v in load(document_id).items()}


def record(document_id: str, page: int, content_kind: str, *, verified_by: str, date: str | None = None) -> dict[str, Any]:
    from ..ingest.metadata import load_metadata
    if content_kind not in KINDS:
        raise PageClassError(f"content_kind must be one of {list(KINDS)}")
    if not verified_by or not verified_by.replace(".", "").isalnum() or len(verified_by) > 12:
        raise PageClassError("verified_by: initials or an annotator ID (letters and digits, at most 12)")
    meta = load_metadata(document_id)
    if meta is None or not 1 <= page <= meta.page_count:
        raise PageClassError("unknown document or page")
    classifier = meta.pages[page - 1].classification.content_kind
    pages = load(document_id)
    pages[page] = {"content_kind": content_kind, "classifier": classifier, "verified_by": verified_by,
                   "date": date or dt.date.today().isoformat()}
    write_canonical(path_for(document_id), {"schema": "redactor.page_classes", "schema_version": "0.1.0",
                                            "document_id": document_id,
                                            "pages": {str(k): v for k, v in sorted(pages.items())}})
    return {"document_id": document_id, "page": page, "content_kind": content_kind, "classifier": classifier,
            "agrees": content_kind == classifier, "verified_pages": len(pages), "page_count": meta.page_count}


def status(document_id: str) -> dict[str, Any]:
    from ..ingest.metadata import load_metadata
    meta = load_metadata(document_id)
    v = load(document_id)
    pages = []
    for pm in (meta.pages if meta else []):
        got = v.get(pm.page)
        pages.append({"page": pm.page, "classifier": pm.classification.content_kind,
                      "verified": got["content_kind"] if got else None, "verified_by": got["verified_by"] if got else None})
    return {"document_id": document_id, "page_count": len(pages), "verified_pages": len(v), "pages": pages}


def e0_metrics(document_ids: list[str]) -> dict[str, Any]:
    """Accuracy and confusion matrix (classifier -> verified) over the verified pages; counts only."""
    from ..ingest.metadata import load_metadata
    conf: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    n = ok = total_pages = 0
    by_doc = {}
    for d in sorted(document_ids):
        meta = load_metadata(d)
        if meta is None:
            continue
        total_pages += meta.page_count
        v = load(d)
        dn = dok = 0
        for pm in meta.pages:
            got = v.get(pm.page)
            if not got:
                continue
            pred, gold = pm.classification.content_kind, got["content_kind"]
            conf[pred][gold] += 1
            n += 1
            dn += 1
            ok += pred == gold
            dok += pred == gold
        by_doc[d] = {"verified": dn, "correct": dok, "pages": meta.page_count}
    return {"pages": total_pages, "verified_pages": n, "accuracy": round(ok / n, 4) if n else None, "correct": ok,
            "confusion_classifier_to_verified": {k: dict(sorted(x.items())) for k, x in sorted(conf.items())},
            "by_document": by_doc, "status": "complete" if n and n == total_pages else ("partial" if n else "not_started")}


def effective_classes(document_id: str, classifier: Mapping[int, str]) -> dict[int, str]:
    """Verified class where a person confirmed one, else the classifier's."""
    v = verified_classes(document_id)
    return {p: v.get(p, c) for p, c in sorted(classifier.items())}
