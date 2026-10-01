"""Per-annotator annotation storage (plan §3.2, §9.2-§9.4), used by the API and the CLI.

- Files: annotations_raw/<A1|A2>/<doc>.ann.json (one file per pass, never edited after submission),
  annotations_raw/claude_silver/ (read-only), annotations/<doc>.ann.json (adjudicated gold).
- Saves are validated (P6 validators) and revision-checked: the client sends the revision it edited;
  a mismatch is refused, so two windows cannot overwrite each other.
- Blind rules: on the IAA documents A1 and A2 see neither the silver pass, nor candidates, nor each
  other's work. Candidates exist only for dev documents outside the IAA set; the test split never
  gets them. The silver pass is a starting point for A1 on the other documents (verification).
- Actions are derived from the policy on save (facts, not policy), unless an entity is overridden.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any

from ..core.canonical import read_json, sha256_file, write_canonical
from ..detectors.baseline import validators as idv
from ..paths import golden_dir
from ..taxonomy import load_policy, load_taxonomy
from .models import AnnotationSet, CanonicalRegistry, Coverage, Provenance
from .register import dataset_config, load_manifest
from .validate import Report, check_annotation_set

HUMAN = ("A1", "A2")
ANNOTATORS = ("A1", "A2", "claude_silver", "adjudicated", "A1_retest")   # A1_retest: test-retest pages (plan §9.3)
VIEWERS = ("A1", "A2", "adjudicator")
MATTER = "matter_001"


class StoreError(ValueError):
    def __init__(self, code: str, status: int = 400, detail: Any = None):
        super().__init__(code)
        self.code = code
        self.status = status
        self.detail = detail


def _check(annotator: str) -> None:
    if annotator not in ANNOTATORS:
        raise StoreError("unknown_annotator", 404)


def annotation_path(annotator: str, document_id: str) -> Path:
    _check(annotator)
    if annotator == "adjudicated":
        return golden_dir() / "annotations" / f"{document_id}.ann.json"
    return golden_dir() / "annotations_raw" / annotator / f"{document_id}.ann.json"


def status_path(annotator: str, document_id: str) -> Path:
    return annotation_path(annotator, document_id).with_suffix("").with_suffix(".status.json")


def registry_path(annotator: str) -> Path:
    _check(annotator)
    if annotator == "adjudicated":
        return golden_dir() / "registry" / f"{MATTER}.entities.json"
    return golden_dir() / "annotations_raw" / annotator / "registry" / f"{MATTER}.entities.json"


def iaa_documents() -> set[str]:
    return set(dataset_config().get("iaa_documents", []))


def split_of(document_id: str) -> str:
    for d in load_manifest().documents:
        if d.document_id == document_id:
            return d.split
    raise StoreError("unknown_document", 404)


def candidates_allowed(document_id: str) -> bool:
    return split_of(document_id) == "dev" and document_id not in iaa_documents()


def can_view(viewer: str, annotator: str, document_id: str) -> bool:
    if viewer not in VIEWERS:
        return False
    if viewer == "adjudicator":
        return True
    if annotator == viewer:
        return True
    if annotator == "A1_retest":
        return viewer == "A1"
    if annotator == "claude_silver":
        return viewer == "A1" and document_id not in iaa_documents()
    if annotator == "adjudicated":
        return document_id not in iaa_documents()
    return False  # A1 never sees A2's work and vice versa


def can_write(viewer: str, annotator: str) -> bool:
    if annotator == "claude_silver":
        return False
    if annotator == "adjudicated":
        return viewer == "adjudicator"
    if annotator == "A1_retest":
        return viewer == "A1"
    return viewer == annotator


def load_status(annotator: str, document_id: str) -> dict[str, Any]:
    p = status_path(annotator, document_id)
    return read_json(p) if p.exists() else {"submitted": False, "revision": 0}


def load(annotator: str, document_id: str) -> AnnotationSet | None:
    p = annotation_path(annotator, document_id)
    return AnnotationSet.model_validate(read_json(p)) if p.exists() else None


def skeleton(annotator: str, document_id: str) -> AnnotationSet:
    from ..ingest.metadata import load_metadata
    meta = load_metadata(document_id)
    if meta is None:
        raise StoreError("unknown_document", 404)
    blind = document_id in iaa_documents()
    mode = "adjudicated" if annotator == "adjudicated" else ("blind" if blind or not candidates_allowed(document_id) else "candidates_shown")
    return AnnotationSet(document_id=document_id, document_sha256=meta.sha256, annotator=annotator, pass_=1, mode=mode,
                         revision=1, guidelines_version=_guidelines_version(), policy_ref=load_policy().ref,
                         coverage=Coverage(), entities=[])


def _guidelines_version() -> str:
    from ..paths import docs_dir
    first = (docs_dir() / "annotation_guidelines.md").read_text(encoding="utf-8").splitlines()[0]
    return first.split("v")[-1].strip() if "v" in first else "0.1.0"


def load_registry(annotator: str) -> CanonicalRegistry | None:
    p = registry_path(annotator)
    return CanonicalRegistry.model_validate(read_json(p)) if p.exists() else None


def apply_policy(ann: AnnotationSet) -> AnnotationSet:
    """Derive every policy-sourced action and the checksum attribute (facts, not policy)."""
    policy, tax = load_policy(), load_taxonomy()
    ents = []
    for e in ann.entities:
        attrs = dict(e.attributes)
        kind = tax.validator(e.entity_type)
        if kind and e.text:
            attrs["checksum"] = "valid" if idv.validate(kind, e.text) else "invalid"
        upd: dict[str, Any] = {"attributes": attrs}
        if e.action_source == "policy":
            upd["action"] = policy.decide(e.entity_type, e.role, attrs, kind).action
        ents.append(e.model_copy(update=upd))
    return ann.model_copy(update={"entities": ents})


def validate(ann: AnnotationSet, annotator: str) -> Report:
    from ..ingest.metadata import load_metadata
    report = Report(path=f"{annotator}/{ann.document_id}.ann.json")
    check_annotation_set(ann, report, path=annotation_path(annotator, ann.document_id), registry=load_registry(annotator),
                         metadata=load_metadata(ann.document_id))
    return report


def save(viewer: str, annotator: str, document_id: str, ann: AnnotationSet, base_revision: int) -> dict[str, Any]:
    if not can_write(viewer, annotator):
        raise StoreError("read_only", 403)
    if ann.document_id != document_id or ann.annotator != annotator:
        raise StoreError("document_or_annotator_mismatch", 400)
    status = load_status(annotator, document_id)
    if status.get("submitted"):
        raise StoreError("submitted_files_are_final", 409)
    current = load(annotator, document_id)
    current_rev = current.revision if current else 0
    if base_revision != current_rev:
        raise StoreError("revision_conflict", 409, {"current_revision": current_rev})
    ann = apply_policy(ann.model_copy(update={"revision": current_rev + 1}))
    report = validate(ann, annotator)
    if not report.ok:
        raise StoreError("validation_failed", 422, sorted({i.code for i in report.errors}))
    sha = write_canonical(annotation_path(annotator, document_id), ann.dump())
    write_canonical(status_path(annotator, document_id), {"submitted": False, "revision": ann.revision, "sha256": sha})
    return {"revision": ann.revision, "sha256": sha[:12], "warnings": sorted({i.code for i in report.warnings})}


def submit(viewer: str, annotator: str, document_id: str) -> dict[str, Any]:
    if not can_write(viewer, annotator) or annotator not in HUMAN:
        raise StoreError("read_only", 403)
    ann = load(annotator, document_id)
    if ann is None:
        raise StoreError("nothing_to_submit", 404)
    report = validate(ann, annotator)
    if not report.ok:
        raise StoreError("validation_failed", 422, sorted({i.code for i in report.errors}))
    st = {"submitted": True, "revision": ann.revision, "sha256": sha256_file(annotation_path(annotator, document_id)),
          "submitted_on": _dt.date.today().isoformat()}
    write_canonical(status_path(annotator, document_id), st)
    return st


def start_from_silver(viewer: str, document_id: str) -> dict[str, Any]:
    """A1's verification file starts as a copy of the silver pass (non-IAA documents only)."""
    if viewer != "A1" or document_id in iaa_documents():
        raise StoreError("silver_not_visible", 403)
    if load("A1", document_id) is not None:
        raise StoreError("file_exists", 409)
    silver = load("claude_silver", document_id)
    if silver is None:
        raise StoreError("no_silver", 404)
    sk = skeleton("A1", document_id)
    ents = [e.model_copy(update={"provenance": Provenance(annotator="A1", pass_=1, origin="silver")}) for e in silver.entities]
    ann = sk.model_copy(update={"mode": "silver", "entities": ents,
                                "coverage": silver.coverage.model_copy(update={"pages_verified": []})})
    return save("A1", "A1", document_id, ann, 0)


def promote(document_id: str, *, source: str, verified_by: str, verified_date: str) -> dict[str, Any]:
    """Copy a verified annotator file into annotations/ (adjudicated gold). Silver-origin entities must
    carry verified_silver + verified_by + date; otherwise the validator refuses the promotion."""
    if not verified_by or not verified_date:
        raise StoreError("verified_by_and_date_required", 400)
    src = load(source, document_id)
    if src is None:
        raise StoreError("no_source_file", 404)
    if not load_status(source, document_id).get("submitted"):
        raise StoreError("source_not_submitted", 409)
    ents = []
    for e in src.entities:
        prov = e.provenance
        if prov.origin == "silver" and prov.adjudication != "verified_silver":
            raise StoreError("unverified_silver_entity", 422, e.entity_id)
        if prov.origin == "silver" and (not prov.verified_by or not prov.verified_date):
            prov = prov.model_copy(update={"verified_by": verified_by, "verified_date": verified_date})
        ents.append(e.model_copy(update={"provenance": prov.model_copy(update={"annotator": "adjudicated"})}))
    current = load("adjudicated", document_id)
    ann = src.model_copy(update={"annotator": "adjudicated", "mode": "adjudicated", "entities": ents, "silver": None,
                                 "revision": current.revision if current else 1})
    return save("adjudicator", "adjudicated", document_id, ann, current.revision if current else 0)
