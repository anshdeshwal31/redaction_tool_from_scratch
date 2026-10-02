"""/annotations (per-annotator namespace), /registry, /candidates, /iaa and /promote (plan §4.10, §9).

The viewing annotator is the `X-Annotator` header (A1, A2 or adjudicator). It is a local workflow
switch, not authentication: the API is reachable from this machine only.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from fastapi import APIRouter, Body, Header, HTTPException
from pydantic import BaseModel

from ...core.canonical import read_json, sha256_obj, write_canonical
from ...dataset import store
from ...dataset.models import AnnotationSet, CanonicalRegistry

router = APIRouter()


def _viewer(x_annotator: str | None) -> str:
    if x_annotator not in store.VIEWERS:
        raise HTTPException(400, "X-Annotator must be A1, A2 or adjudicator")
    return x_annotator


class SaveBody(BaseModel):
    base_revision: int
    annotation: dict[str, Any]


@router.get("/annotations/{annotator}/{doc}")
def get_annotation(annotator: str, doc: str, x_annotator: str | None = Header(None)) -> dict[str, Any]:
    viewer = _viewer(x_annotator)
    if not store.can_view(viewer, annotator, doc):
        raise HTTPException(403, "blind: not visible to this annotator")
    ann = store.load(annotator, doc)
    status = store.load_status(annotator, doc)
    if ann is None:
        if annotator == "claude_silver":
            raise HTTPException(404, "no_silver")
        ann = store.skeleton(annotator, doc)
        stored_revision = 0
    else:
        stored_revision = ann.revision
    return {"annotation": ann.dump(), "stored_revision": stored_revision, "status": status,
            "readonly": not store.can_write(viewer, annotator) or bool(status.get("submitted"))}


@router.put("/annotations/{annotator}/{doc}")
def put_annotation(annotator: str, doc: str, body: SaveBody, x_annotator: str | None = Header(None)) -> dict[str, Any]:
    viewer = _viewer(x_annotator)
    try:
        ann = AnnotationSet.model_validate(body.annotation)
    except ValueError as exc:  # pydantic ValidationError: field paths only, never values
        errs = getattr(exc, "errors", lambda: [])()
        raise HTTPException(422, {"error": "schema", "fields": sorted({".".join(str(x) for x in e.get("loc", ())) for e in errs})[:50]})
    return store.save(viewer, annotator, doc, ann, body.base_revision)


@router.post("/annotations/{annotator}/{doc}/submit")
def submit(annotator: str, doc: str, x_annotator: str | None = Header(None)) -> dict[str, Any]:
    return store.submit(_viewer(x_annotator), annotator, doc)


@router.post("/annotations/A1/{doc}/start-from-silver")
def start_from_silver(doc: str, x_annotator: str | None = Header(None)) -> dict[str, Any]:
    return store.start_from_silver(_viewer(x_annotator), doc)


@router.post("/promote/{doc}")
def promote(doc: str, source: str = Body(...), verified_by: str = Body(...), x_annotator: str | None = Header(None)) -> dict[str, Any]:
    if _viewer(x_annotator) != "adjudicator":
        raise HTTPException(403, "adjudicator only")
    return store.promote(doc, source=source, verified_by=verified_by, verified_date=_dt.date.today().isoformat())


# ---------------------------------------------------------------- registry
@router.get("/registry/{annotator}")
def get_registry(annotator: str, x_annotator: str | None = Header(None)) -> dict[str, Any]:
    viewer = _viewer(x_annotator)
    if viewer != "adjudicator" and annotator != viewer and annotator != "adjudicated":
        raise HTTPException(403, "blind")
    reg = store.load_registry(annotator)
    data = reg.dump() if reg else CanonicalRegistry(matter_id=store.MATTER, annotator=annotator, entities=[]).dump()
    return {"registry": data, "sha": sha256_obj(data)[:16]}


class RegistryBody(BaseModel):
    base_sha: str
    registry: dict[str, Any]


@router.put("/registry/{annotator}")
def put_registry(annotator: str, body: RegistryBody, x_annotator: str | None = Header(None)) -> dict[str, Any]:
    viewer = _viewer(x_annotator)
    if not store.can_write(viewer, annotator):
        raise HTTPException(403, "read_only")
    current = store.load_registry(annotator)
    cur = current.dump() if current else CanonicalRegistry(matter_id=store.MATTER, annotator=annotator, entities=[]).dump()
    if sha256_obj(cur)[:16] != body.base_sha:
        raise HTTPException(409, "registry_conflict")
    reg = CanonicalRegistry.model_validate(body.registry)
    data = reg.dump()
    write_canonical(store.registry_path(annotator), data)
    return {"sha": sha256_obj(data)[:16], "entities": len(reg.entities)}


# ---------------------------------------------------------------- candidates
@router.get("/candidates/{doc}")
def candidates(doc: str, detector: str = "union", x_annotator: str | None = Header(None)) -> dict[str, Any]:
    """Candidates (plan §9.5): by default the union of the baseline and Presidio, so pre-annotation does not
    lean toward the baseline; every span keeps its `detector`. Falls back to the baseline alone (and says
    so) when Presidio is not installed."""
    viewer = _viewer(x_annotator)
    if not store.candidates_allowed(doc):
        raise HTTPException(403, "no candidates on the test split or the IAA documents")
    if viewer == "A2":
        raise HTTPException(403, "A2 works blind")
    from ...detectors.base import create, run_detector
    from ...experiments.runner import load_document_text
    if detector not in ("union", "baseline", "baseline_v1"):
        raise HTTPException(400, "unknown detector")
    d = load_document_text(doc)
    used, note = [detector], None
    if detector == "union":
        from ...detectors.combiner import Combined
        try:
            det = Combined([create("baseline"), create("presidio")], "union")
            used = ["baseline", "presidio"]
            spans = run_detector(det, [d])[doc]
        except Exception as exc:  # noqa: BLE001 - Presidio missing: baseline only, reported by class name
            used, note = ["baseline"], f"presidio unavailable ({type(exc).__name__})"
            spans = run_detector(create("baseline"), [d])[doc]
    else:
        spans = run_detector(create(detector), [d])[doc]
    return {"document_id": doc, "detector": "+".join(used), "note": note, "spans": [s.to_dict() for s in spans]}


# ---------------------------------------------------------------- IAA
@router.get("/iaa/{doc}")
def iaa(doc: str, a: str = "A1", b: str = "A2", x_annotator: str | None = Header(None)) -> dict[str, Any]:
    if _viewer(x_annotator) != "adjudicator":
        raise HTTPException(403, "adjudicator only")
    from ...evaluation.gold import load_gold
    from ...evaluation.iaa import compare
    from ...experiments.runner import load_document_text, page_classes_for
    from ...taxonomy import load_policy, load_taxonomy
    ga = load_gold(doc, path=store.annotation_path(a, doc)) if store.annotation_path(a, doc).exists() else None
    gb = load_gold(doc, path=store.annotation_path(b, doc)) if store.annotation_path(b, doc).exists() else None
    if ga is None or gb is None:
        raise HTTPException(404, "both annotation files are needed")
    pub, priv = compare(load_document_text(doc), ga, gb, policy=load_policy(), taxonomy=load_taxonomy(),
                        page_classes=page_classes_for(doc))
    return {"public": pub, "worklist": priv}



# ---------------------------------------------------------------- page-class verification (plan §9.2 P1)
@router.get("/page-classes/{doc}")
def page_classes(doc: str, x_annotator: str | None = Header(None)) -> dict[str, Any]:
    _viewer(x_annotator)
    from ...dataset import page_classes as pc
    return pc.status(doc)


class PageClassBody(BaseModel):
    content_kind: str
    verified_by: str


@router.put("/page-classes/{doc}/{page}")
def verify_page_class(doc: str, page: int, body: PageClassBody, x_annotator: str | None = Header(None)) -> dict[str, Any]:
    if _viewer(x_annotator) not in ("A1", "adjudicator"):
        raise HTTPException(403, "page classes are verified by A1 or the adjudicator")
    from ...dataset import page_classes as pc
    try:
        return pc.record(doc, page, body.content_kind, verified_by=body.verified_by)
    except pc.PageClassError as exc:
        raise HTTPException(422, str(exc))
