"""/documents, /extractions and /taxonomy. Original filenames and source paths are never returned."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from ...dataset import store
from ...dataset.register import document_path, load_manifest
from ...extraction import cache
from ...extraction.render import png_bytes, render_page, renders_dir
from ...ingest.metadata import load_metadata
from ...taxonomy import (DATE_ROLES, ENTITY_TYPES, LOCATION_GRANULARITY, ORG_ROLES, PERSON_ROLES, load_policy,
                         load_taxonomy)

router = APIRouter()
FLAGS = ["handwritten", "ocr_degraded", "split_line", "split_page", "partially_illegible", "quasi_identifier"]


def _meta(doc: str):
    meta = load_metadata(doc)
    if meta is None:
        raise HTTPException(404, "unknown_document")
    return meta


@router.get("/documents")
def documents() -> list[dict[str, Any]]:
    out = []
    iaa = store.iaa_documents()
    for d in load_manifest().documents:
        meta = load_metadata(d.document_id)
        classes: dict[str, int] = {}
        for p in (meta.pages if meta else []):
            classes[p.classification.content_kind] = classes.get(p.classification.content_kind, 0) + 1
        out.append({"document_id": d.document_id, "split": d.split, "page_count": meta.page_count if meta else None,
                    "page_classes": dict(sorted(classes.items())), "iaa": d.document_id in iaa,
                    "candidates_allowed": store.candidates_allowed(d.document_id),
                    "document_type": meta.document_type if meta else None})
    return sorted(out, key=lambda x: x["document_id"])


@router.get("/documents/{doc}")
def document(doc: str) -> dict[str, Any]:
    meta = _meta(doc)
    pages = []
    for p in meta.pages:
        pages.append({"page": p.page, "width_pt": p.width_pt, "height_pt": p.height_pt, "rotation": p.rotation,
                      "content_kind": p.classification.content_kind, "ocr_required": p.classification.ocr_required,
                      "extraction": p.extraction.model_dump() if p.extraction else None})
    return {"document_id": doc, "split": store.split_of(doc), "page_count": meta.page_count, "pages": pages,
            "document_type": meta.document_type, "iaa": doc in store.iaa_documents(),
            "candidates_allowed": store.candidates_allowed(doc)}


@router.get("/documents/{doc}/pages/{page}/image", responses={200: {"content": {"image/png": {}}}})
def page_image(doc: str, page: int, dpi: int = Query(100, ge=50, le=200)) -> Response:
    meta = _meta(doc)
    if not 1 <= page <= meta.page_count:
        raise HTTPException(404, "unknown_page")
    out = renders_dir(doc, dpi) / f"p{page:04d}.png"
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp")
        tmp.write_bytes(png_bytes(render_page(document_path(doc), page, dpi, gray=False)))
        tmp.replace(out)
    return Response(out.read_bytes(), media_type="image/png")


@router.get("/documents/{doc}/pages/{page}/words")
def page_words(doc: str, page: int) -> dict[str, Any]:
    pages = cache.load_reference_pages(doc)
    pe = pages.get(page)
    if pe is None:
        raise HTTPException(404, "no_extraction")
    return {"document_id": doc, "page": page, "text_source_id": pe.text_source_id, "method": pe.method,
            "width_pt": pe.width_pt, "height_pt": pe.height_pt, "text": pe.text,
            "words": [w.to_dict() for w in pe.words]}


@router.get("/extractions/{doc}")
def extraction_sources(doc: str) -> dict[str, Any]:
    """Text sources cached for a document (plan §4.10 `/extractions`): IDs and page counts only."""
    _meta(doc)
    out = {}
    for sid in cache.list_sources(doc):
        out[sid] = sorted(p for p in range(1, _meta(doc).page_count + 1) if cache.has_page(doc, sid, p))
    ref = {p: pe.text_source_id for p, pe in sorted(cache.load_reference_pages(doc).items())}
    return {"document_id": doc, "sources": {k: {"pages": v} for k, v in sorted(out.items())}, "reference": ref}


@router.get("/extractions/{doc}/pages/{page}")
def extraction_words(doc: str, page: int, source: str = Query(..., min_length=3, max_length=300)) -> dict[str, Any]:
    """Words and boxes of one page for one text source (local UI only; the app refuses non-loopback clients)."""
    pe = cache.load_page(doc, source, page)
    if pe is None:
        raise HTTPException(404, "no_extraction_for_source")
    return {"document_id": doc, "page": page, "text_source_id": pe.text_source_id, "method": pe.method,
            "width_pt": pe.width_pt, "height_pt": pe.height_pt, "text": pe.text, "words": [w.to_dict() for w in pe.words]}


@router.get("/documents/{doc}/fields")
def fields(doc: str) -> dict[str, Any]:
    _meta(doc)
    return {"document_id": doc, "fields": cache.load_fields(doc)}


@router.get("/taxonomy")
def taxonomy() -> dict[str, Any]:
    tax = load_taxonomy()
    return {"entity_types": [t for t in ENTITY_TYPES], "region_only": [t for t in ENTITY_TYPES if tax.region_only(t)],
            "person_roles": list(PERSON_ROLES), "org_roles": list(ORG_ROLES), "date_roles": list(DATE_ROLES),
            "location_granularity": list(LOCATION_GRANULARITY), "flags": FLAGS,
            "certainty": ["certain", "probable", "uncertain"], "policy": load_policy().ref,
            "groups": {t: tax.group(t) for t in ENTITY_TYPES}}
