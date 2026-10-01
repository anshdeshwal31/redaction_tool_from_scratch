"""Seeded verification sample for the silver trust rule (plan §9.4).

Test-split documents are verified in full; IAA documents count as verified through the from-scratch
A1/A2 path; every other document gets a class-stratified sample of `rate` of its pages (at least
`min_pages`, all pages if fewer). Selection is SHA-256 ranking of (seed, document, page): no RNG,
no hash(), identical on every machine. The sample is written to the manifest once and never redrawn.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from ..core.canonical import sha256_hex
from .models import VerificationSample


class SampleError(ValueError):
    pass


def _rank(seed: str, document_id: str, page: int) -> str:
    return sha256_hex(f"{seed}|{document_id}|{page}".encode("utf-8"))


def sample_size(n_pages: int, rate: float, min_pages: int) -> int:
    if n_pages <= min_pages:
        return n_pages
    return min(n_pages, max(min_pages, math.ceil(rate * n_pages)))


def allocate(by_class: Mapping[str, list[int]], k: int) -> dict[str, int]:
    """Largest-remainder allocation of k pages across classes, proportional to class size."""
    total = sum(len(v) for v in by_class.values())
    if k > total:
        raise SampleError("sample larger than document")
    classes = sorted(c for c, v in by_class.items() if v)
    quotas = {c: k * len(by_class[c]) / total for c in classes}
    alloc = {c: int(math.floor(q)) for c, q in quotas.items()}
    rest = k - sum(alloc.values())
    for c in sorted(classes, key=lambda c: (-(quotas[c] - alloc[c]), c))[:rest]:
        alloc[c] += 1
    return alloc


def stratified_pages(document_id: str, page_classes: Mapping[int, str], seed: str, rate: float, min_pages: int) -> list[int]:
    by_class: dict[str, list[int]] = {}
    for page, cls in sorted(page_classes.items()):
        by_class.setdefault(cls, []).append(page)
    k = sample_size(len(page_classes), rate, min_pages)
    chosen: list[int] = []
    for cls, n in sorted(allocate(by_class, k).items()):
        ranked = sorted(by_class[cls], key=lambda p: (_rank(seed, document_id, p), p))
        chosen += ranked[:n]
    return sorted(chosen)


def draw(documents: Mapping[str, Mapping[int, str]], *, test_split: list[str], iaa: list[str], seed: str,
         rate: float, min_pages: int) -> tuple[VerificationSample, dict[str, Any]]:
    """documents: doc_id -> {page: page class}. Returns the sample and a per-document summary."""
    pages: dict[str, list[int]] = {}
    summary: dict[str, Any] = {}
    for doc in sorted(documents):
        classes = documents[doc]
        if doc in iaa:  # verified in full by the blind A1/A2 path (this also covers a test document)
            summary[doc] = {"rule": "iaa_from_scratch", "pages": 0}
        elif doc in test_split:
            pages[doc] = sorted(classes)
            summary[doc] = {"rule": "test_split_full", "pages": len(pages[doc])}
        else:
            pages[doc] = stratified_pages(doc, classes, seed, rate, min_pages)
            summary[doc] = {"rule": "seeded_sample", "pages": len(pages[doc]), "of": len(classes)}
    return VerificationSample(seed=seed, drawn_before_verification=True, pages=pages), summary


def draw_for_dataset(*, force: bool = False) -> dict[str, Any]:
    from ..ingest.metadata import load_metadata
    from .register import dataset_config, load_manifest, save_manifest
    cfg = dataset_config()
    vs_cfg = cfg["verification_sample"]
    manifest = load_manifest()
    if manifest.verification_sample is not None and not force:
        return {"ok": True, "status": "already_drawn", "seed": manifest.verification_sample.seed,
                "pages": {d: len(p) for d, p in sorted(manifest.verification_sample.pages.items())}}
    docs = {}
    for d in manifest.documents:
        meta = load_metadata(d.document_id)
        docs[d.document_id] = {pm.page: pm.classification.content_kind for pm in meta.pages}
    sample, summary = draw(docs, test_split=list(cfg["splits"]["test"]), iaa=list(cfg.get("iaa_documents", [])),
                           seed=str(vs_cfg["seed"]), rate=float(vs_cfg["rate"]), min_pages=int(vs_cfg["min_pages"]))
    manifest.verification_sample = sample
    sha = save_manifest(manifest)
    return {"ok": True, "status": "drawn", "seed": sample.seed, "documents": summary, "manifest_sha256": sha[:12]}
