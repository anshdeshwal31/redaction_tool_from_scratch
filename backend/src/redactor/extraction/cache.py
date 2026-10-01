"""Extraction cache (plan §4.1 S1): one canonical JSON file per (document, text source, page).

Layout (confidential, under data/):
    data/extraction/<doc>/<source_key>/p0001.json    PageExtraction
    data/extraction/<doc>/sources.json               text_source_id -> source_key
    data/extraction/<doc>/reference.json             page -> reference text_source_id
    data/extraction/<doc>/fields.json                document-level field values (filename, pdf.*)
"""

from __future__ import annotations

import re
from pathlib import Path

from ..core.canonical import read_json, sha256_hex, write_canonical
from ..core.types import PageExtraction
from ..paths import data_dir

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def extraction_root() -> Path:
    return data_dir() / "extraction"


def source_key(text_source_id: str) -> str:
    return f"{_SAFE.sub('_', text_source_id)[:60]}-{sha256_hex(text_source_id)[:8]}"


def doc_dir(document_id: str) -> Path:
    return extraction_root() / document_id


def page_path(document_id: str, text_source_id: str, page: int) -> Path:
    return doc_dir(document_id) / source_key(text_source_id) / f"p{page:04d}.json"


def save_page(pe: PageExtraction) -> str:
    sources_file = doc_dir(pe.document_id) / "sources.json"
    sources = read_json(sources_file) if sources_file.exists() else {}
    if sources.get(pe.text_source_id) != source_key(pe.text_source_id):
        sources[pe.text_source_id] = source_key(pe.text_source_id)
        write_canonical(sources_file, sources)
    return write_canonical(page_path(pe.document_id, pe.text_source_id, pe.page), pe.to_dict())


def load_page(document_id: str, text_source_id: str, page: int) -> PageExtraction | None:
    p = page_path(document_id, text_source_id, page)
    if not p.exists():
        return None
    return PageExtraction.from_dict(read_json(p))


def has_page(document_id: str, text_source_id: str, page: int) -> bool:
    return page_path(document_id, text_source_id, page).exists()


def list_sources(document_id: str) -> list[str]:
    f = doc_dir(document_id) / "sources.json"
    return sorted(read_json(f)) if f.exists() else []


def save_reference(document_id: str, mapping: dict[int, str]) -> None:
    write_canonical(doc_dir(document_id) / "reference.json", {str(k): v for k, v in sorted(mapping.items())})


def load_reference(document_id: str) -> dict[int, str]:
    f = doc_dir(document_id) / "reference.json"
    return {int(k): v for k, v in read_json(f).items()} if f.exists() else {}


def save_fields(document_id: str, fields: dict[str, str]) -> None:
    write_canonical(doc_dir(document_id) / "fields.json", fields)


def load_fields(document_id: str) -> dict[str, str]:
    f = doc_dir(document_id) / "fields.json"
    return read_json(f) if f.exists() else {}


def load_reference_pages(document_id: str) -> dict[int, PageExtraction]:
    out: dict[int, PageExtraction] = {}
    for page, source in sorted(load_reference(document_id).items()):
        pe = load_page(document_id, source, page)
        if pe is not None:
            out[page] = pe
    return out
