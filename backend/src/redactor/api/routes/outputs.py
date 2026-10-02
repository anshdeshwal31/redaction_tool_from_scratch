"""/export and /reidentify (plan §4.10, §4.12). Localhost only (app middleware), role-gated, audit-logged.

/export answers 409 while the gate is not clear for a document; nothing is exported for it. Responses
carry counts, statuses and hashes; restored text is returned only to the local caller and never logged.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Response
from pydantic import BaseModel

from ...security.access import allowed

router = APIRouter()


class ExportBody(BaseModel):
    formats: list[str] = ["json", "text", "md", "pdf"]


@router.post("/export/{matter}")
def export(matter: str, body: ExportBody, x_actor: str | None = Header(None)) -> dict[str, Any]:
    from ...output.exporter import ExportRefused, export_matter
    if not allowed("export", x_actor):
        raise HTTPException(403, "actor is not authorised for export")
    bad = sorted(set(body.formats) - {"json", "text", "md", "pdf"})
    if bad:
        raise HTTPException(422, f"unknown formats: {bad}")
    try:
        rep = export_matter(matter, formats=body.formats)
    except ExportRefused as exc:
        raise HTTPException(409, str(exc))
    if rep["exported"] == 0:
        raise HTTPException(409, detail={"message": "gate not clear: nothing exported", "documents": rep["documents"]})
    return rep


class ReidentifyBody(BaseModel):
    text: str


@router.post("/reidentify/{matter}")
def reidentify(matter: str, body: ReidentifyBody, x_actor: str | None = Header(None)) -> dict[str, Any]:
    from ...reidentify.service import ReidentifyError, restore_text
    if not allowed("reidentify", x_actor):
        raise HTTPException(403, "actor is not authorised for re-identification")
    try:
        restored, report = restore_text(matter, body.text, actor=x_actor or "", channel="api")
    except ReidentifyError as exc:
        raise HTTPException(404, str(exc))
    return {"text": restored, "report": report}


@router.get("/preview/{matter}/{doc}")
def preview(matter: str, doc: str) -> dict[str, Any]:
    """Redaction preview (U1), local only: the staged pseudonymised pages with their gate status."""
    from ...core.canonical import read_json
    from ...output.exporter import staged_paths
    sp, _ = staged_paths(matter, doc)
    if not sp.exists():
        raise HTTPException(404, "no staged output: run the pipeline for this matter")
    d = read_json(sp)
    return {"document_id": doc, "gate": d["gate"], "output_sha256": d["output_sha256"],
            "pages": [{"page": p["page"], "content_kind": p["content_kind"], "gate": p["gate"], "text": p["text"],
                       "spans": len(p["spans"])} for p in d["pages"]]}


@router.get("/preview/{matter}/{doc}/{page}/image", responses={200: {"content": {"image/png": {}}}})
def preview_image(matter: str, doc: str, page: int, dpi: int = Query(100, ge=50, le=200)) -> Response:
    """The page as the PDF export would burn it (boxes over every protected span and region)."""
    from ...core.canonical import read_json
    from ...dataset.register import document_path
    from ...extraction.render import png_bytes, render_page
    from ...output import pdf as pdfmod
    from ...output.exporter import staged_paths
    sp, priv = staged_paths(matter, doc)
    if not sp.exists():
        raise HTTPException(404, "no staged output")
    d = read_json(sp)
    pg = next((p for p in d["pages"] if p["page"] == page), None)
    if pg is None:
        raise HTTPException(404, "unknown page")
    regions = [r for r in (read_json(priv).get("regions", []) if priv.exists() else []) if r.get("page") == page]
    img, _ = pdfmod.burn(render_page(document_path(doc), page, dpi), pdfmod.page_boxes(pg, regions), dpi)
    return Response(png_bytes(img), media_type="image/png", headers={"Cache-Control": "no-store"})


@router.get("/export/{matter}/status")
def export_status_route(matter: str) -> dict[str, Any]:
    """Exported documents and whether a later vault epoch made them stale (counts and IDs only)."""
    from ...output.exporter import export_status
    from ...pipeline import load_state
    st = load_state(matter)
    if st is None:
        raise HTTPException(404, "no gate run for this matter")
    return export_status(matter, int(st.get("epoch", 1)))
