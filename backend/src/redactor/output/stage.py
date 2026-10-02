"""Staging of the X1 outputs after a gate run: one `redaction.pseudonymized_document` per document and
its private edit-log sidecar (original offsets, keyed hashes of originals, burn regions), both under
data/outputs/<matter>/ until export."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ..core.canonical import write_canonical
from .export import build, opaque_ids


def stage(matter_id: str, res, vault, page_info: Mapping[str, Mapping[int, Mapping[str, Any]]], pipeline_meta: Mapping[str, Any],
          out_dir: Path) -> dict[str, str]:
    from ..dataset.register import load_manifest
    shas = {d.document_id: d.sha256 for d in load_manifest().documents}
    gids = [e.group_id for r in res.matter.documents.values() for e in r.edits if e.group_id]
    ids = opaque_ids(gids, matter_id)
    gate_pages = {(p.document_id, p.page): {"state": p.state, "reasons": sorted(set(p.reasons))} for p in res.pages}
    hits: dict[str, int] = {}
    for h in res.hits:
        hits[h.document_id] = hits.get(h.document_id, 0) + 1
    out: dict[str, str] = {}
    for d, r in sorted(res.matter.documents.items()):
        doc, priv = build(d, matter_id, shas.get(d, ""), res.chosen[d], r, ids=ids, matter_key=vault.matter_key,
                          page_classes={p: v["class"] for p, v in page_info.get(d, {}).items()},
                          gate_pages={p: gp for (dd, p), gp in gate_pages.items() if dd == d}, leak_hits=hits.get(d, 0),
                          pipeline=pipeline_meta)
        priv["regions"] = [x for x in res.regions if x.get("document_id") == d]
        write_canonical(out_dir / f"{d}.export.json", doc)
        write_canonical(out_dir / "private" / f"{d}.edit_log.private.json", priv)
        out[d] = doc["output_sha256"]
    return out
