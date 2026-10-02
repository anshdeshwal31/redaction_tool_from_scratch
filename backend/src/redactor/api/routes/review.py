"""/review: the REVIEW queue and reviewer decisions (plan §4.10, §4.11). Decisions are appended to the
log with reviewer, time and reason; a re-run of the matter applies them (decisions are pipeline inputs)."""

from __future__ import annotations

import subprocess
import sys
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from ...gate import queue
from ...paths import REPO_ROOT
from ...pipeline import load_state

router = APIRouter()
_RUNS: dict[str, subprocess.Popen] = {}


@router.get("/review/{matter}")
def review_state(matter: str) -> dict[str, Any]:
    st = load_state(matter)
    if st is None:
        raise HTTPException(404, "no gate run for this matter yet")
    decided = queue.latest(queue.load_decisions(matter))
    for it in st["items"]:
        d = decided.get(it["item_id"])
        it["decision"] = d["decision"] if d else None
        it["resolved_now"] = queue.resolves(it, d)
    return st


class DecisionBody(BaseModel):
    item_id: str
    decision: str
    reason: str | None = None
    payload: dict[str, Any] | None = None


@router.post("/review/{matter}/decisions")
def decide(matter: str, body: DecisionBody, x_annotator: str | None = Header(None), x_reviewer: str | None = Header(None)) -> dict[str, Any]:
    reviewer = x_reviewer or x_annotator
    st = load_state(matter)
    if st is None:
        raise HTTPException(404, "no gate run")
    item = next((i for i in st["items"] if i["item_id"] == body.item_id), None)
    if item is None:
        raise HTTPException(404, "unknown item")
    try:
        rec = queue.record(matter, item, body.decision, reviewer=reviewer or "", reason=body.reason, payload=body.payload)
    except queue.QueueError as exc:
        raise HTTPException(422, str(exc))
    return {k: v for k, v in rec.items() if k != "payload"}


@router.post("/review/{matter}/rerun")
def rerun(matter: str) -> dict[str, Any]:
    if matter in _RUNS and _RUNS[matter].poll() is None:
        return {"state": "running"}
    _RUNS[matter] = subprocess.Popen([sys.executable, "-m", "redactor.cli", "pipeline", "run", "--matter", matter],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(REPO_ROOT / "backend"))
    return {"state": "started"}


@router.get("/review/{matter}/page/{doc}/{page}")
def review_page(matter: str, doc: str, page: int) -> dict[str, Any]:
    """Boxes for the page's span items on the text source the gate chose (leak hits refer to the output
    text, so they are listed without boxes)."""
    from ...extraction import cache
    from ...gate.gate import span_boxes
    st = load_state(matter)
    if st is None:
        raise HTTPException(404, "no gate run")
    pg = next((p for p in st["pages"] if p["document_id"] == doc and p["page"] == page), None)
    if pg is None:
        raise HTTPException(404, "unknown page")
    pe = cache.load_page(doc, pg["chosen_source"], page) or cache.load_reference_pages(doc).get(page)
    items = []
    for it in st["items"]:
        if it["document_id"] != doc or it["page"] != page:
            continue
        boxes = []
        if pe is not None and it.get("start") is not None and not it["reason"].startswith("leak_"):
            boxes = [b.to_dict() for b in span_boxes(pe, it["start"], it["end"])]
        items.append({**it, "boxes": boxes})
    return {"page": pg, "width_pt": pe.width_pt if pe else None, "height_pt": pe.height_pt if pe else None, "items": items}


@router.get("/review/{matter}/rerun")
def rerun_state(matter: str) -> dict[str, Any]:
    p = _RUNS.get(matter)
    if p is None:
        return {"state": "idle"}
    return {"state": "running" if p.poll() is None else ("done" if p.returncode == 0 else "failed")}
