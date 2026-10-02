"""`redaction.pseudonymized_document` v0.1 (plan §4.12.1).

No original text anywhere in the file: page texts are the pseudonymised texts, the edit log carries
only output offsets, canonical IDs are opaque per-matter ordinals (the linker's internal group IDs are
hashes of normalised names and are never exported). A local sidecar holds the original offsets and a
keyed hash of each original, for audit and the edit-completeness test. `output_sha256` is the hash of
the canonical JSON without that field.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping, Sequence

from ..core.canonical import canonical_json, sha256_hex
from ..core.types import DocumentText
from ..replacement.engine import DocResult, Edit
from ..replacement.keys import hmac_hex

SCHEMA = "redaction.pseudonymized_document"
VERSION = "0.1.0"


def opaque_ids(group_ids: Sequence[str], matter_id: str) -> dict[str, str]:
    """person:3fa2... -> matter_001/person_007 (ordinal per type in sorted group-ID order)."""
    out: dict[str, str] = {}
    n: dict[str, int] = defaultdict(int)
    for gid in sorted(set(g for g in group_ids if g)):
        kind = gid.split(":", 1)[0]
        kind = {"organization": "org"}.get(kind, kind)
        n[kind] += 1
        out[gid] = f"{matter_id}/{kind}_{n[kind]:03d}"
    return out


def apply_with_offsets(text: str, edits: Sequence[Edit]) -> tuple[str, list[tuple[Edit, int, int]]]:
    out, placed, shift, pos = [], [], 0, 0
    for e in sorted(edits, key=lambda e: e.start):
        out.append(text[pos:e.start])
        ns = e.start + shift
        out.append(e.replacement)
        placed.append((e, ns, ns + len(e.replacement)))
        shift += len(e.replacement) - (e.end - e.start)
        pos = e.end
    out.append(text[pos:])
    return "".join(out), placed


def fallback_boxes(pe, start: int, end: int):
    """Fail-closed box for a span with no word box (plan §4.12.3): the whole line(s) of the nearest
    words around it, or the whole page when the page has no words."""
    from ..core.types import BBox
    if not pe.words:
        return [BBox(0.0, 0.0, float(pe.width_pt), float(pe.height_pt))]
    before = [w for w in pe.words if w.end <= start]
    after = [w for w in pe.words if w.start >= end]
    lines = {w.line for w in ([max(before, key=lambda w: w.end)] if before else []) + ([min(after, key=lambda w: w.start)] if after else [])}
    out = []
    for ln in sorted(lines):
        ws = [w.bbox for w in pe.words if w.line == ln]
        u = BBox.union_all(ws)
        out.append(BBox(0.0, u.y0, float(pe.width_pt), u.y1))
    return out


def build(document_id: str, matter_id: str, source_sha256: str, doc: DocumentText, result: DocResult, *,
          ids: Mapping[str, str], matter_key: bytes, page_classes: Mapping[int, str], gate_pages: Mapping[int, Mapping[str, Any]] | None,
          leak_hits: int, pipeline: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    from ..gate.gate import span_boxes
    pages, edit_log, private = [], [], []
    n_edit = 0
    for p, pe in sorted(doc.pages.items()):
        edits = [e for e in result.edits if e.page == p]
        new_text, placed = apply_with_offsets(pe.text, edits)
        if new_text != result.pages.get(p, new_text):
            raise ValueError("edit log does not reproduce the output text")
        spans = []
        for k, (e, ns, ne) in enumerate(placed, 1):
            n_edit += 1
            sid = f"p{p}.s{k:04d}"
            boxes = [b.to_dict() for b in (span_boxes(pe, e.start, e.end) or fallback_boxes(pe, e.start, e.end))]
            cid = ids.get(e.group_id or "")
            spans.append({"span_id": sid, "start": ns, "end": ne, "entity_type": e.entity_type, "canonical_id": cid,
                          "action": e.action, "bboxes": boxes})
            eid = f"e{n_edit:04d}"
            edit_log.append({"edit_id": eid, "page": p, "span_id": sid, "new_start": ns, "new_end": ne, "entity_type": e.entity_type,
                             "canonical_id": cid, "action": e.action, "strategy": e.strategy, "rule_id": e.rule})
            private.append({"edit_id": eid, "page": p, "orig_start": e.start, "orig_end": e.end, "orig_len": e.end - e.start,
                            "orig_hmac": hmac_hex(matter_key, "ORIG", pe.text[e.start:e.end])})
        g = (gate_pages or {}).get(p, {})
        pages.append({"page": p, "content_kind": page_classes.get(p, "unknown"), "text_source_id": pe.text_source_id, "text": new_text,
                      "gate": {"status": g.get("state", "NOT_RUN"), "reasons": list(g.get("reasons", []))}, "spans": spans})
    fields = {}
    for f, t in sorted(doc.fields.items()):
        edits = [e for e in result.edits if e.field == f]
        fields[f], placed = apply_with_offsets(t, edits)
        for e, ns, ne in placed:
            n_edit += 1
            eid = f"e{n_edit:04d}"
            edit_log.append({"edit_id": eid, "page": None, "field": f, "span_id": None, "new_start": ns, "new_end": ne,
                             "entity_type": e.entity_type, "canonical_id": ids.get(e.group_id or ""), "action": e.action,
                             "strategy": e.strategy, "rule_id": e.rule})
            private.append({"edit_id": eid, "field": f, "orig_start": e.start, "orig_end": e.end, "orig_len": e.end - e.start,
                            "orig_hmac": hmac_hex(matter_key, "ORIG", t[e.start:e.end])})
    states = [pg["gate"]["status"] for pg in pages]
    status = "BLOCKED" if "BLOCKED" in states else ("REVIEW" if "REVIEW" in states else ("NOT_RUN" if "NOT_RUN" in states else "PASS"))
    out = {"schema": SCHEMA, "schema_version": VERSION, "document_id": document_id, "matter_id": matter_id,
           "source_sha256": source_sha256, "pipeline": dict(pipeline), "pages": pages, "fields": fields, "edit_log": edit_log,
           "gate": {"status": status, "leak_scan": "clean" if leak_hits == 0 else f"{leak_hits} hits"}}
    out["output_sha256"] = sha256_hex(canonical_json(out))
    return out, {"schema": "redaction.edit_log_private", "document_id": document_id, "edits": private}


def verify_sha(doc: Mapping[str, Any]) -> bool:
    body = {k: v for k, v in doc.items() if k != "output_sha256"}
    return sha256_hex(canonical_json(body)) == doc.get("output_sha256")
