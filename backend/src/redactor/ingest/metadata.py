"""Document metadata writer (plan §3.3) and document-level field capture (plan §3.4 `field`).

The metadata file (golden_dataset/metadata/, confidential) records structure only plus the
original filename. PDF metadata *values* (Title, Author, XMP, …) are stored for annotation in
the confidential extraction cache (data/extraction/<doc>/fields.json), never in metadata.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pikepdf

from ..core.canonical import write_canonical
from ..dataset.models import DocumentMetadata, PageMeta, PdfInfo
from ..dataset.register import dataset_config, document_path, load_manifest, source_file_for
from ..extraction import cache
from ..paths import golden_dir
from .classifier import classify, load_config, signals_of
from .pdfinfo import inspect_document

INFO_FIELDS = {"/Title": "pdf.title", "/Author": "pdf.author", "/Subject": "pdf.subject", "/Keywords": "pdf.keywords"}
XMP_KEYS = ("dc:title", "dc:creator", "dc:description", "dc:subject", "pdf:Keywords", "pdf:Producer",
            "xmp:CreatorTool", "pdfx:Author", "xmpMM:DocumentID")


def metadata_path(document_id: str) -> Path:
    return golden_dir() / "metadata" / f"{document_id}.meta.json"


def _pdf_info_and_fields(path: Path) -> tuple[PdfInfo, dict[str, str]]:
    fields: dict[str, str] = {}
    with pikepdf.open(path) as pdf:
        info = pdf.docinfo
        present = []
        for key, name in INFO_FIELDS.items():
            if key in info and str(info[key]).strip():
                present.append(name.split(".", 1)[1])
                fields[name] = str(info[key])
        producer = str(info["/Producer"]) if "/Producer" in info else None
        creator = str(info["/Creator"]) if "/Creator" in info else None
        xmp_values: list[str] = []
        if "/Metadata" in pdf.Root:
            present.append("xmp")
            try:
                meta = pdf.open_metadata()
                for k in XMP_KEYS:
                    if k in meta and str(meta[k]).strip():
                        xmp_values.append(f"{k}: {meta[k]}")
            except Exception:  # noqa: BLE001 - unreadable XMP is recorded as present only
                pass
        if xmp_values:
            fields["xmp"] = "\n".join(xmp_values)
        annots = sum(len(p.obj.get("/Annots", [])) for p in pdf.pages)
        acro = pdf.Root.get("/AcroForm")
        form_fields = len(acro.get("/Fields", [])) if acro is not None else 0
        embedded = len(pdf.attachments)
        return PdfInfo(version=str(pdf.pdf_version), producer=producer, creator=creator,
                       encrypted=bool(pdf.is_encrypted), metadata_fields_present=sorted(present),
                       annotations=annots, form_fields=form_fields, embedded_files=embedded), fields


def build_metadata(document_id: str) -> tuple[DocumentMetadata, dict[str, str]]:
    manifest = load_manifest()
    doc = next(d for d in manifest.documents if d.document_id == document_id)
    cfg = load_config()
    copy = document_path(document_id)
    src = source_file_for(document_id)
    original_name = src.name if src is not None else "<unavailable>"
    structures = inspect_document(copy, grid=cfg.raw["coverage_grid"], small_path_max_pt=cfg.raw["small_path_max_pt"])
    pages = []
    for ps in structures:
        signals = signals_of(ps)
        pages.append(PageMeta(page=ps.page, width_pt=round(ps.width_pt, 2), height_pt=round(ps.height_pt, 2),
                              rotation=ps.rotation, signals=signals, classification=classify(signals, cfg)))
    pdf_info, fields = _pdf_info_and_fields(copy)
    fields["filename"] = original_name
    ds = dataset_config()
    meta = DocumentMetadata(
        document_id=document_id, matter_id=doc.matter_id, split=doc.split, original_filename=original_name,
        source_path=f"golden_dataset_docs/{original_name}", sha256=doc.sha256, size_bytes=doc.size_bytes,
        document_type=ds.get("document_types", {}).get(document_id, "unknown"), pdf=pdf_info,
        page_count=len(pages), pages=pages)
    return meta, fields


def write_metadata(document_id: str, meta: DocumentMetadata, fields: dict[str, str]) -> str:
    cache.save_fields(document_id, dict(sorted(fields.items())))
    return write_canonical(metadata_path(document_id), meta.dump())


def load_metadata(document_id: str) -> DocumentMetadata | None:
    from ..core.canonical import read_json
    p = metadata_path(document_id)
    return DocumentMetadata.model_validate(read_json(p)) if p.exists() else None


def classify_all() -> dict[str, Any]:
    """Classify every registered document; aggregate-only summary."""
    manifest = load_manifest()
    summary: dict[str, Any] = {"documents": {}, "totals": {}}
    totals: dict[str, int] = {}
    for d in manifest.documents:
        meta, fields = build_metadata(d.document_id)
        sha = write_metadata(d.document_id, meta, fields)
        kinds: dict[str, int] = {}
        for p in meta.pages:
            k = p.classification.content_kind
            kinds[k] = kinds.get(k, 0) + 1
            totals[k] = totals.get(k, 0) + 1
        ocr_pages = sum(1 for p in meta.pages if p.classification.ocr_scope == "full_page")
        summary["documents"][d.document_id] = {
            "pages": meta.page_count, "split": meta.split, "classes": dict(sorted(kinds.items())),
            "ocr_full_page": ocr_pages,
            "raster_region_pages": sum(1 for p in meta.pages if p.classification.ocr_scope == "raster_regions"),
            "hybrid_pages": [p.page for p in meta.pages if p.classification.content_kind == "hybrid"],
            "metadata_fields_present": meta.pdf.metadata_fields_present, "metadata_sha256": sha[:12],
        }
    summary["totals"] = dict(sorted(totals.items()))
    summary["pages"] = sum(v["pages"] for v in summary["documents"].values())
    summary["classifier"] = load_config().fingerprint
    return summary
