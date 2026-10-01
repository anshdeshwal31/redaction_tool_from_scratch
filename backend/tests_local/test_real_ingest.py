"""Real-data checks (plan §12 smoke run). Aggregate assertions only: counts, IDs, hash prefixes.

Run with: uv run pytest tests_local -q
"""

from __future__ import annotations

from redactor.dataset.register import dataset_config, load_manifest, verify_hashes
from redactor.ingest.metadata import load_metadata


def test_register_matches_plan():
    m = load_manifest()
    assert [d.document_id for d in m.documents] == ["doc_001", "doc_002", "doc_003", "doc_004", "doc_005"]
    expected = dataset_config()["expected_sha256_prefixes"]
    assert {d.document_id: d.sha256[:12] for d in m.documents} == expected
    assert {d.document_id: d.split for d in m.documents} == {
        "doc_001": "test", "doc_002": "dev", "doc_003": "dev", "doc_004": "test", "doc_005": "dev"}


def test_hashes_unchanged():
    assert verify_hashes()["ok"]


def test_page_classes_match_plan():
    totals: dict[str, int] = {}
    pages = 0
    hybrid = []
    for doc in ("doc_001", "doc_002", "doc_003", "doc_004", "doc_005"):
        meta = load_metadata(doc)
        assert meta is not None
        pages += meta.page_count
        for p in meta.pages:
            totals[p.classification.content_kind] = totals.get(p.classification.content_kind, 0) + 1
            if p.classification.content_kind == "hybrid":
                hybrid.append((doc, p.page))
    assert pages == 88
    assert totals == {"vector_outlined": 9, "scanned": 52, "born_digital": 26, "hybrid": 1}
    assert hybrid == [("doc_003", 1)]
