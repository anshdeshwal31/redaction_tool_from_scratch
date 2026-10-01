"""Annotation-campaign tooling (A2 is human work; this is its tooling, plan §9.2-§9.4).

- recall audit (P7): pool every local detector over every text source; spans that touch no gold
  mention become audit items, accepted into gold (`origin=audit_added`) or rejected with a reason
  from a fixed list. Decisions are recorded in golden_dataset/audit/<doc>.audit.json.
- page transcripts (OCR subset): seeded from the reference OCR, verified by a person.
- explicit schema migrations (v0.1 -> v0.2 after the pilot); originals are kept.
- freeze: validate everything, renumber gold entity IDs by a fixed sort (page, y0, x0, type),
  record every file hash in the manifest. verify: revalidate and compare hashes with the manifest.
The assistant never commits or tags the golden repository; the owner does (`--commit` flag).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable

from ..core.canonical import read_json, sha256_file, write_canonical
from ..paths import golden_dir
from .models import SCHEMA_MODELS, AnnotationSet, PageTranscript, TranscriptLine
from .validate import Report, validate_file

REJECT_REASONS = ("not_pii", "kept_public_body", "generic_term", "ocr_noise")
# Every local deterministic detector plus the P7 scans (plan §9.4). Philter is not pooled: its output is not
# deterministic as shipped (C1), and §9.4 requires annotation artifacts to come from deterministic detectors.
AUDIT_DETECTORS = ("baseline", "baseline_v1", "presidio", "openredaction", "audit_scans")
SKIP_PARTS = {"work", ".git"}


class CampaignError(ValueError):
    pass


# ---------------------------------------------------------------- recall audit (P7)
def audit_pool(document_id: str, detectors: tuple[str, ...] = AUDIT_DETECTORS) -> dict[str, Any]:
    """Write audit items: predicted spans from the pooled detectors that overlap no gold mention."""
    from ..detectors.base import available, create, run_detector
    from ..evaluation.gold import load_gold, project_document
    from ..evaluation.matching import overlap
    from ..evaluation.metrics import gold_mspan, pred_mspan
    from ..experiments.runner import load_document_text
    from ..taxonomy import load_policy, load_taxonomy
    doc = load_document_text(document_id)
    gold = load_gold(document_id)
    if gold is None:
        raise CampaignError("no gold or silver file to audit against")
    mentions = project_document(gold, doc, policy=load_policy(), taxonomy=load_taxonomy())
    gms = [gold_mspan(m) for m in mentions if m.projected]
    items: dict[tuple, dict[str, Any]] = {}
    used: list[str] = []
    skipped: dict[str, str] = {}
    pooled: list[tuple[str, list]] = []
    for name in detectors:
        if name not in available():
            skipped[name] = "not_registered"
            continue
        try:
            pooled.append((name, run_detector(create(name), [doc])[document_id]))
            used.append(name)
        except Exception as exc:  # noqa: BLE001 - a missing sidecar or model skips that detector; class name only
            skipped[name] = type(exc).__name__
    for name, spans in pooled:
        for s in spans:
            pm = pred_mspan(s)
            if any(overlap(pm, g) > 0 for g in gms):
                continue
            key = (s.page or -1, s.field or "", s.start, s.end)
            it = items.setdefault(key, {"page": s.page, "field": s.field, "text_source_id": s.text_source_id, "start": s.start,
                                        "end": s.end, "text": s.text, "types": [], "detectors": [], "status": "pending"})
            if s.entity_type not in it["types"]:
                it["types"].append(s.entity_type)
            if name not in it["detectors"]:
                it["detectors"].append(name)
    path = golden_dir() / "audit" / f"{document_id}.audit.json"
    prev = read_json(path) if path.exists() else {"items": []}
    decided = {(i["page"] or -1, i["field"] or "", i["start"], i["end"]): i for i in prev["items"] if i["status"] != "pending"}
    out = []
    for key in sorted(items):
        out.append(decided.get(key, items[key]))
    write_canonical(path, {"schema": "redactor.audit", "schema_version": "0.1.0", "document_id": document_id,
                           "against": gold.path.split("golden_dataset")[-1], "detectors": used, "skipped": skipped, "items": out})
    return {"document_id": document_id, "items": len(out), "pending": sum(1 for i in out if i["status"] == "pending"),
            "detectors": used, "skipped": skipped}


def audit_decide(document_id: str, index: int, decision: str, *, entity_type: str | None = None, reason: str | None = None) -> None:
    path = golden_dir() / "audit" / f"{document_id}.audit.json"
    data = read_json(path)
    item = data["items"][index]
    if decision == "accept":
        if not entity_type:
            raise CampaignError("accept needs the entity type")
        item.update({"status": "accepted", "entity_type": entity_type})
    elif decision == "reject":
        if reason not in REJECT_REASONS:
            raise CampaignError(f"reject reason must be one of {REJECT_REASONS}")
        item.update({"status": "rejected", "reason": reason})
    else:
        raise CampaignError("decision must be accept or reject")
    write_canonical(path, data)


# ---------------------------------------------------------------- transcripts (OCR subset)
def seed_transcript(document_id: str, page: int) -> dict[str, Any]:
    from ..core.types import BBox
    from ..dataset.models import Region
    from ..extraction import cache
    pe = cache.load_reference_pages(document_id).get(page)
    if pe is None:
        raise CampaignError("no extraction for this page")
    lines: dict[int, list] = {}
    for w in pe.words:
        lines.setdefault(w.line, []).append(w)
    out = []
    for ln in sorted(lines):
        ws = lines[ln]
        b = BBox.union_all(w.bbox for w in ws)
        out.append(TranscriptLine(text=" ".join(w.text for w in ws), bbox=Region(x0=round(b.x0, 2), y0=round(b.y0, 2), x1=round(b.x1, 2), y1=round(b.y1, 2))))
    t = PageTranscript(document_id=document_id, page=page, lines=out, seeded_from=pe.text_source_id, verified_by=None)
    path = golden_dir() / "transcripts" / document_id / f"p{page:04d}.json"
    if path.exists():
        raise CampaignError("transcript exists; it is edited by a person, never re-seeded")
    write_canonical(path, t.dump())
    return {"document_id": document_id, "page": page, "lines": len(out)}


def load_transcripts(document_id: str, *, verified_only: bool = True) -> dict[int, str]:
    d = golden_dir() / "transcripts" / document_id
    out = {}
    for p in sorted(d.glob("p*.json")) if d.exists() else []:
        t = PageTranscript.model_validate(read_json(p))
        if verified_only and not t.verified_by:
            continue
        out[t.page] = "\n".join(line.text for line in t.lines)
    return out


# ---------------------------------------------------------------- migrations
MIGRATIONS: dict[tuple[str, str], Callable[[dict[str, Any]], dict[str, Any]]] = {}


def migration(src: str, dst: str):
    def deco(fn):
        MIGRATIONS[(src, dst)] = fn
        return fn
    return deco


@migration("0.1.0", "0.2.0")
def _v01_to_v02(raw: dict[str, Any]) -> dict[str, Any]:
    """Placeholder until the pilot fixes schema v0.2: carries every field over and bumps the version.
    Changes decided at the pilot are added here as explicit steps (never by hand-editing files)."""
    out = dict(raw)
    out["schema_version"] = "0.2.0"
    return out


def migrate_file(path: Path, to_version: str, *, keep_original: bool = True) -> dict[str, Any]:
    raw = read_json(path)
    src = raw.get("schema_version")
    if src == to_version:
        return {"file": path.name, "status": "already", "version": src}
    fn = MIGRATIONS.get((src, to_version))
    if fn is None:
        raise CampaignError(f"no migration {src} -> {to_version}")
    if keep_original:
        orig = path.with_name(path.name + f".v{src}.orig")
        if not orig.exists():
            orig.write_bytes(path.read_bytes())
    new = fn(raw)
    write_canonical(path, new)
    return {"file": path.name, "status": "migrated", "from": src, "to": to_version}


# ---------------------------------------------------------------- freeze / verify
def _tracked_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file() and not (set(p.relative_to(root).parts) & SKIP_PARTS)
                  and p.name != "manifest.json" and not p.name.endswith(".orig"))


def renumber(ann: AnnotationSet) -> AnnotationSet:
    """Entity IDs by a fixed sort (page, y0, x0, type, field, start) so equal content gives equal bytes."""
    def key(e):
        r = e.regions[0] if e.regions else None
        return (e.page or 0, e.field or "", round(r.y0, 1) if r else 0.0, round(r.x0, 1) if r else 0.0, e.entity_type,
                e.text_anchor.start if e.text_anchor else 0, e.entity_id)
    ents = sorted(ann.entities, key=key)
    return ann.model_copy(update={"entities": [e.model_copy(update={"entity_id": f"{ann.document_id}.e{i:04d}"}) for i, e in enumerate(ents, 1)]})


def freeze(dataset_version: str, *, commit: bool = False) -> dict[str, Any]:
    from .register import load_manifest, save_manifest
    root = golden_dir()
    reports: list[Report] = []
    gold_dir = root / "annotations"
    for p in sorted(gold_dir.glob("*.ann.json")) if gold_dir.exists() else []:
        ann = renumber(AnnotationSet.model_validate(read_json(p)))
        write_canonical(p, ann.dump())
    for p in _tracked_files(root):
        if p.suffix == ".json" and read_json(p).get("schema") in SCHEMA_MODELS:
            reports.append(validate_file(p))
    errors = sum(len(r.errors) for r in reports)
    if errors:
        return {"ok": False, "errors": errors, "files": len(reports)}
    m = load_manifest()
    m.dataset_version = dataset_version
    m.files = {str(p.relative_to(root)).replace("\\", "/"): sha256_file(p) for p in _tracked_files(root)}
    sha = save_manifest(m)
    out: dict[str, Any] = {"ok": True, "dataset_version": dataset_version, "files": len(m.files), "manifest_sha256": sha[:12],
                           "committed": False}
    if commit:  # only when the owner runs it: the golden repo is local-only (pre-push refuses)
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", f"golden_dataset v{dataset_version}"], check=True)
        subprocess.run(["git", "-C", str(root), "tag", f"v{dataset_version}"], check=True)
        out["committed"] = True
    return out


def verify() -> dict[str, Any]:
    from .register import load_manifest
    root = golden_dir()
    m = load_manifest()
    current = {str(p.relative_to(root)).replace("\\", "/"): sha256_file(p) for p in _tracked_files(root)}
    changed = sorted(k for k in m.files if k in current and current[k] != m.files[k])
    missing = sorted(k for k in m.files if k not in current)
    added = sorted(k for k in current if k not in m.files)
    errors = 0
    for p in _tracked_files(root):
        if p.suffix == ".json" and read_json(p).get("schema") in SCHEMA_MODELS:
            errors += len(validate_file(p).errors)
    ok = bool(m.files) and not changed and not missing and not added and errors == 0
    return {"ok": ok, "dataset_version": m.dataset_version, "files_frozen": len(m.files), "changed": len(changed),
            "missing": len(missing), "added": len(added), "validation_errors": errors}
