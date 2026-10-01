"""`dataset validate` (plan §9.4 validators). Reports never contain entity text: only codes,
entity IDs, pages and counts, so the output is safe to show anywhere.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from pydantic import ValidationError

from ..core.canonical import read_json
from ..detectors.baseline import validators as idv
from ..paths import golden_dir
from ..taxonomy import DATE_ROLES, LOCATION_GRANULARITY, ORG_ROLES, PERSON_ROLES, load_policy, load_taxonomy
from .models import SCHEMA_MODELS, AnnotationSet, CanonicalRegistry, DocumentMetadata

PAGE_TOLERANCE_PT = 1.5


@dataclass
class Issue:
    level: str  # error | warning
    code: str
    entity_id: str | None = None
    page: int | None = None
    detail: str = ""  # never document text

    def as_dict(self) -> dict[str, Any]:
        return {"level": self.level, "code": self.code, "entity_id": self.entity_id, "page": self.page, "detail": self.detail}


@dataclass
class Report:
    path: str
    schema: str | None = None
    issues: list[Issue] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    def error(self, code: str, **kw) -> None:
        self.issues.append(Issue("error", code, **kw))

    def warn(self, code: str, **kw) -> None:
        self.issues.append(Issue("warning", code, **kw))

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors


TextLookup = Callable[[str, str, int | None, str | None], str | None]


def default_text_lookup(document_id: str, text_source_id: str, page: int | None, field_name: str | None) -> str | None:
    from ..extraction import cache
    if field_name is not None:
        return cache.load_fields(document_id).get(field_name)
    if page is None:
        return None
    pe = cache.load_page(document_id, text_source_id, page)
    return pe.text if pe is not None else None


def _pydantic_issues(report: Report, exc: ValidationError) -> None:
    for err in exc.errors(include_input=False, include_url=False):
        loc = ".".join(str(p) for p in err.get("loc", ()))
        report.error("schema", detail=f"{loc}: {err.get('type')}")


def validate_file(path: str | Path, *, text_lookup: TextLookup | None = default_text_lookup,
                  registry: CanonicalRegistry | None = None) -> Report:
    path = Path(path)
    report = Report(path=str(path))
    try:
        raw = read_json(path)
    except Exception as exc:  # noqa: BLE001 - report the type only (no payload)
        report.error("unreadable", detail=type(exc).__name__)
        return report
    schema = raw.get("schema") if isinstance(raw, dict) else None
    report.schema = schema
    model = SCHEMA_MODELS.get(schema or "")
    if model is None:
        report.error("unknown_schema")
        return report
    try:
        obj = model.model_validate(raw)
    except ValidationError as exc:
        _pydantic_issues(report, exc)
        return report
    if isinstance(obj, AnnotationSet):
        check_annotation_set(obj, report, path=path, text_lookup=text_lookup, registry=registry)
    elif isinstance(obj, CanonicalRegistry):
        check_registry(obj, report)
    elif isinstance(obj, DocumentMetadata):
        report.counts["pages"] = obj.page_count
        if obj.page_count != len(obj.pages):
            report.error("page_count_mismatch")
    return report


def _load_metadata(document_id: str) -> DocumentMetadata | None:
    p = golden_dir() / "metadata" / f"{document_id}.meta.json"
    if not p.exists():
        return None
    try:
        return DocumentMetadata.model_validate(read_json(p))
    except ValidationError:
        return None


def _age_on(dob: _dt.date, ref: _dt.date) -> int:
    years = ref.year - dob.year
    if (ref.month, ref.day) < (dob.month, dob.day):
        years -= 1
    return years


def check_annotation_set(ann: AnnotationSet, report: Report, *, path: Path | None = None,
                         text_lookup: TextLookup | None = default_text_lookup,
                         registry: CanonicalRegistry | None = None,
                         metadata: DocumentMetadata | None = None) -> None:
    taxonomy = load_taxonomy()
    policy = load_policy()
    metadata = metadata or _load_metadata(ann.document_id)
    page_sizes = {p.page: (p.width_pt, p.height_pt) for p in metadata.pages} if metadata else {}
    report.counts.update({"entities": len(ann.entities), "pages_complete": len(ann.coverage.pages_complete)})

    # Location rules for silver and promoted files (§9.4, §12).
    if path is not None:
        parts = [p.lower() for p in Path(path).parts]
        in_gold = "annotations" in parts and "annotations_raw" not in parts
        if ann.annotator == "claude_silver" and in_gold:
            report.error("silver_in_gold_folder")
        if ann.annotator == "claude_silver" and "claude_silver" not in parts:
            report.error("silver_outside_silver_folder")

    ids: set[str] = set()
    anchors: dict[tuple, list[tuple[int, int, str]]] = {}
    registry_ids = {e.canonical_id: e for e in registry.entities} if registry else None
    for ent in ann.entities:
        eid = ent.entity_id
        if eid in ids:
            report.error("duplicate_entity_id", entity_id=eid)
        ids.add(eid)
        region_only = taxonomy.region_only(ent.entity_type)

        if ent.page is not None:
            if metadata and ent.page > metadata.page_count:
                report.error("page_out_of_range", entity_id=eid, page=ent.page)
            if not ent.regions:
                report.error("no_regions", entity_id=eid, page=ent.page)
            size = page_sizes.get(ent.page)
            for r in ent.regions:
                if r.x1 - r.x0 <= 0 or r.y1 - r.y0 <= 0:
                    report.error("empty_region", entity_id=eid, page=ent.page)
                if size and (r.x0 < -PAGE_TOLERANCE_PT or r.y0 < -PAGE_TOLERANCE_PT
                             or r.x1 > size[0] + PAGE_TOLERANCE_PT or r.y1 > size[1] + PAGE_TOLERANCE_PT):
                    report.error("region_outside_page", entity_id=eid, page=ent.page)
        if region_only:
            if ent.text is not None or ent.text_anchor is not None:
                report.error("region_only_with_text", entity_id=eid, page=ent.page)
        elif not ent.text:
            report.error("missing_text", entity_id=eid, page=ent.page)

        if ent.field is not None and ent.text_anchor is not None:
            if ent.text_anchor.text_source_id != f"field:{ent.field}":
                report.error("field_anchor_source", entity_id=eid)

        if ent.text_anchor is not None and text_lookup is not None:
            src = text_lookup(ann.document_id, ent.text_anchor.text_source_id, ent.page, ent.field)
            if src is None:
                report.warn("anchor_source_unavailable", entity_id=eid, page=ent.page)
            elif ent.text_anchor.end > len(src):
                report.error("anchor_out_of_range", entity_id=eid, page=ent.page)
            else:
                surface = src[ent.text_anchor.start:ent.text_anchor.end]
                if ent.text is not None and surface != ent.text and "ocr_degraded" not in ent.flags:
                    report.error("anchor_text_mismatch", entity_id=eid, page=ent.page,
                                 detail="anchored source text differs from gold text without the ocr_degraded flag")
            key = (ent.page, ent.field, ent.text_anchor.text_source_id)
            anchors.setdefault(key, []).append((ent.text_anchor.start, ent.text_anchor.end, eid))

        if ent.entity_type in ("PERSON", "DATE_OF_BIRTH") and not ent.canonical_id:
            report.error("missing_canonical_id", entity_id=eid, page=ent.page)
        if ent.canonical_id and registry_ids is not None and ent.canonical_id not in registry_ids:
            report.error("canonical_id_not_in_registry", entity_id=eid)
        if ent.entity_type == "DATE_OF_BIRTH" and registry_ids is not None and ent.canonical_id in registry_ids:
            if registry_ids[ent.canonical_id].entity_type != "PERSON":
                report.error("dob_not_person", entity_id=eid)
        if ({"handwritten", "partially_illegible"} & set(ent.flags)) and not ent.notes.strip():
            report.error("flag_needs_note", entity_id=eid, page=ent.page)

        # Vocabularies.
        if ent.entity_type == "PERSON" and ent.role and ent.role not in PERSON_ROLES:
            report.error("unknown_role", entity_id=eid)
        if ent.entity_type == "ORGANIZATION" and ent.role and ent.role not in ORG_ROLES:
            report.error("unknown_role", entity_id=eid)
        if ent.entity_type == "LOCATION":
            g = ent.attributes.get("granularity")
            if g is not None and g not in LOCATION_GRANULARITY:
                report.error("unknown_granularity", entity_id=eid)
        if ent.entity_type == "DATE":
            dr = ent.attributes.get("date_role")
            if dr is not None and dr not in DATE_ROLES:
                report.error("unknown_date_role", entity_id=eid)

        # Identifier checksums: a mismatch is a verification flag, not an error (§9.4).
        kind = taxonomy.validator(ent.entity_type)
        if kind and ent.text:
            valid = idv.validate(kind, ent.text)
            declared = ent.attributes.get("checksum")
            if declared is not None and declared != ("valid" if valid else "invalid"):
                report.warn("checksum_attribute_mismatch", entity_id=eid)
            if not valid:
                report.warn("checksum_invalid", entity_id=eid, page=ent.page)

        # Action must follow the policy unless overridden.
        if ent.action_source == "policy":
            dec = policy.decide(ent.entity_type, ent.role, ent.attributes, kind)
            if dec.action != ent.action:
                level = report.error if ann.policy_ref == policy.ref else report.warn
                level("action_not_policy", entity_id=eid, detail=f"expected {dec.action}")

        # DOB against stated age (§9.4).
        if ent.entity_type == "AGE" and registry_ids is not None:
            stated = ent.attributes.get("stated_age")
            ref = ent.attributes.get("age_ref_date")
            person = registry_ids.get(ent.canonical_id or "")
            if stated is not None and ref and person and person.date_of_birth:
                try:
                    age = _age_on(_dt.date.fromisoformat(person.date_of_birth), _dt.date.fromisoformat(ref))
                    if age != int(stated):
                        report.warn("dob_age_mismatch", entity_id=eid, page=ent.page)
                except ValueError:
                    report.warn("unparseable_age_check", entity_id=eid)

        # Silver provenance must say silver; promoted silver needs a verifier (§9.4).
        if ann.annotator == "claude_silver" and ent.provenance.origin != "silver":
            report.error("silver_origin_expected", entity_id=eid)
        if ann.annotator != "claude_silver" and ent.provenance.origin == "silver":
            if ent.provenance.adjudication != "verified_silver" or not ent.provenance.verified_by or not ent.provenance.verified_date:
                # gold (annotations/) may only hold verified silver; an A1 verification file is work in progress
                if ann.annotator == "adjudicated":
                    report.error("promoted_silver_unverified", entity_id=eid)
                else:
                    report.warn("silver_not_yet_verified", entity_id=eid)

    for key, spans in anchors.items():
        spans.sort()
        for (s1, e1, id1), (s2, e2, id2) in zip(spans, spans[1:]):
            if s2 < e1:
                report.error("overlapping_spans", entity_id=f"{id1},{id2}", page=key[0])

    if metadata:
        for p in ann.coverage.pages_complete:
            if p < 1 or p > metadata.page_count:
                report.error("coverage_page_out_of_range", page=p)
    if not set(ann.coverage.pages_verified) <= set(ann.coverage.pages_complete):
        report.error("verified_page_not_complete")
    if ann.document_sha256 and metadata and ann.document_sha256 != metadata.sha256:
        report.error("document_sha256_mismatch")


def check_registry(reg: CanonicalRegistry, report: Report) -> None:
    report.counts["entities"] = len(reg.entities)
    for e in reg.entities:
        if not e.canonical_id.startswith(reg.matter_id + "/"):
            report.error("canonical_id_prefix", entity_id=e.canonical_id)
        if e.entity_type == "PERSON" and e.gender not in (None, "unknown") and not e.gender_evidence:
            report.error("gender_without_evidence", entity_id=e.canonical_id)
        if e.date_of_birth:
            try:
                _dt.date.fromisoformat(e.date_of_birth)
            except ValueError:
                report.error("bad_date_of_birth", entity_id=e.canonical_id)


def check_registry_mentions(reg: CanonicalRegistry, annotation_sets: Iterable[AnnotationSet], report: Report) -> None:
    mentioned = {e.canonical_id for a in annotation_sets for e in a.entities if e.canonical_id}
    for e in reg.entities:
        if e.canonical_id not in mentioned:
            report.error("registry_entity_without_mention", entity_id=e.canonical_id)


def summarize(reports: list[Report]) -> dict[str, Any]:
    by_code: dict[str, int] = {}
    for r in reports:
        for i in r.issues:
            by_code[f"{i.level}:{i.code}"] = by_code.get(f"{i.level}:{i.code}", 0) + 1
    return {
        "files": len(reports),
        "files_ok": sum(1 for r in reports if r.ok),
        "errors": sum(len(r.errors) for r in reports),
        "warnings": sum(len(r.warnings) for r in reports),
        "by_code": dict(sorted(by_code.items())),
    }
