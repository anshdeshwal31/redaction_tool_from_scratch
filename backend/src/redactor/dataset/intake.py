"""Document intake (plan §4.10 `/documents` register or upload; §11 U1 document upload).

An uploaded PDF is registered like the originals (opaque doc_id, SHA-256, read-only copy, manifest entry)
but under the `intake` split, so experiments (dev/test) never pick it up and it is never scored. The
upload's filename is discarded (filenames can hold PII); the manifest stores only a hash of
"upload:<sha256>". Classification and extraction run afterwards, then the production pipeline
(`pipeline run --matter`) and the release gate decide what can be exported.
"""

from __future__ import annotations

import os
import stat
from typing import Any

from ..core.canonical import sha256_hex
from .models import ManifestDocument
from .register import document_path, load_manifest, save_manifest

MAX_BYTES = 200 * 1024 * 1024


class IntakeError(ValueError):
    pass


def add_pdf(data: bytes, matter_id: str) -> dict[str, Any]:
    if not data.startswith(b"%PDF-"):
        raise IntakeError("not a PDF")
    if len(data) > MAX_BYTES:
        raise IntakeError("file too large")
    if not matter_id.replace("_", "").isalnum():
        raise IntakeError("invalid matter id")
    sha = sha256_hex(data)
    m = load_manifest()
    for d in m.documents:
        if d.sha256 == sha:
            return {"document_id": d.document_id, "new": False, "matter_id": d.matter_id, "split": d.split}
    n = max((int(d.document_id.split("_")[1]) for d in m.documents), default=0) + 1
    doc_id = f"doc_{n:03d}"
    p = document_path(doc_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_bytes(data)
    if sha256_hex(tmp.read_bytes()) != sha:
        tmp.unlink()
        raise IntakeError("copy hash mismatch")
    tmp.replace(p)
    os.chmod(p, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
    m.documents.append(ManifestDocument(document_id=doc_id, sha256=sha, size_bytes=len(data), matter_id=matter_id, split="intake",
                                        registered_order=len(m.documents) + 1, source_name_sha256=sha256_hex(f"upload:{sha}")))
    m.documents.sort(key=lambda d: d.document_id)
    m.files[f"documents/{doc_id}.pdf"] = sha
    save_manifest(m)
    return {"document_id": doc_id, "new": True, "matter_id": matter_id, "split": "intake"}


def process(document_id: str) -> dict[str, Any]:
    """Classify and extract one intake document (aggregate-only summary)."""
    from ..extraction.pipeline import extract_document
    from ..ingest.metadata import build_metadata, write_metadata
    meta, fields = build_metadata(document_id)
    write_metadata(document_id, meta, fields)
    ex = extract_document(document_id)
    kinds: dict[str, int] = {}
    for pg in meta.pages:
        kinds[pg.classification.content_kind] = kinds.get(pg.classification.content_kind, 0) + 1
    return {"document_id": document_id, "pages": meta.page_count, "classes": dict(sorted(kinds.items())),
            "extraction": {k: v for k, v in ex.items() if isinstance(v, (int, float, str))}}


def status(document_id: str) -> dict[str, Any]:
    from ..extraction import cache
    from ..ingest.metadata import load_metadata
    meta = load_metadata(document_id)
    if meta is None:
        return {"document_id": document_id, "state": "registered"}
    pages = cache.load_reference_pages(document_id)
    state = "extracted" if len(pages) == meta.page_count else "classified"
    return {"document_id": document_id, "state": state, "pages": meta.page_count, "extracted_pages": len(pages)}
