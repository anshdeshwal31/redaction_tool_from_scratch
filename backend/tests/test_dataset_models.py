from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from redactor.core.canonical import write_canonical
from redactor.dataset.models import SCHEMA_MODELS, AnnotationSet, CanonicalRegistry
from redactor.dataset.schema_export import export_schemas
from redactor.dataset.validate import validate_file
from redactor.taxonomy import ENTITY_TYPES, load_policy, load_taxonomy


def _entity(eid, etype="PERSON", text="Jane Example", page=1, start=0, end=12, **kw):
    base = {
        "entity_id": eid, "canonical_id": "matter_001/person_001", "entity_type": etype, "text": text,
        "page": page, "regions": [{"x0": 10, "y0": 10, "x1": 60, "y1": 20}],
        "text_anchor": {"text_source_id": "src", "start": start, "end": end},
        "role": "plaintiff", "action": load_policy().decide(etype, kw.get("role", "plaintiff")).action,
        "provenance": {"annotator": "A1", "pass": 1, "origin": "manual"},
    }
    base.update(kw)
    return base


def _ann(entities, annotator="A1", **kw):
    d = {"schema": "golden.annotation_set", "document_id": "doc_900", "document_sha256": "ab" * 32,
         "annotator": annotator, "mode": "blind", "guidelines_version": "0.1.0",
         "policy_ref": load_policy().ref, "coverage": {"pages_complete": [1]}, "entities": entities}
    d.update(kw)
    return d


def test_taxonomy_matches_code():
    t = load_taxonomy()
    assert set(t.types) == set(ENTITY_TYPES)
    assert t.compatible_types("DATE", "DATE_OF_BIRTH")
    assert t.validator("MEDICARE") == "medicare"


def test_roundtrip_and_aliases():
    a = AnnotationSet.model_validate(_ann([_entity("e1")]))
    dumped = a.dump()
    assert dumped["schema"] == "golden.annotation_set" and dumped["pass"] == 1
    assert AnnotationSet.model_validate(dumped) == a


def test_silver_rules():
    with pytest.raises(ValidationError):
        AnnotationSet.model_validate(_ann([], annotator="claude_silver", mode="silver"))
    silver = {"model_id": "m", "run_date": "2026-10-01", "guidelines_version": "0.1.0",
              "prompt_sha256": "0" * 64, "input_sha256s": {}}
    AnnotationSet.model_validate(_ann([], annotator="claude_silver", mode="silver", silver=silver))
    with pytest.raises(ValidationError):
        AnnotationSet.model_validate(_ann([], silver=silver))


def test_entity_needs_page_or_field():
    with pytest.raises(ValidationError):
        AnnotationSet.model_validate(_ann([_entity("e1", page=None)]))


def test_schema_export(tmp_path):
    written = export_schemas(tmp_path)
    assert len(written) == len(SCHEMA_MODELS) + 1          # + redaction.pseudonymized_document (X1)
    assert any(n.startswith("redaction.pseudonymized_document.") for n in written)
    for name in written:
        schema = json.loads((tmp_path / name).read_text(encoding="utf-8"))
        assert schema["type"] == "object" and "properties" in schema
        # every $ref resolves inside the document
        refs = [r for r in json.dumps(schema).split('"$ref": "#/$defs/')[1:]]
        for r in refs:
            assert r.split('"')[0] in schema.get("$defs", {})


def _lookup(text):
    return lambda doc, src, page, field: text


def test_validate_reports_codes_without_text(tmp_path):
    text = "Jane Example saw Dr Bob Other"
    ents = [
        _entity("e1", start=0, end=12),
        _entity("e2", text="Jane Exampel", start=0, end=12),               # mismatch without ocr_degraded
        _entity("e3", etype="PERSON", text="Bob Other", start=20, end=29, canonical_id=None),
        _entity("e3", etype="DATE", text="Jane", start=0, end=4, role=None,  # duplicate id, overlap
                action="KEEP", flags=["handwritten"]),
    ]
    p = tmp_path / "doc_900.ann.json"
    write_canonical(p, _ann(ents))
    r = validate_file(p, text_lookup=_lookup(text))
    codes = {i.code for i in r.errors}
    assert {"anchor_text_mismatch", "missing_canonical_id", "duplicate_entity_id",
            "overlapping_spans", "flag_needs_note"} <= codes
    blob = json.dumps([i.as_dict() for i in r.issues])
    assert "Jane" not in blob and "Bob" not in blob


def test_validate_silver_location(tmp_path):
    silver = {"model_id": "m", "run_date": "2026-10-01", "guidelines_version": "0.1.0",
              "prompt_sha256": "0" * 64, "input_sha256s": {}}
    ent = _entity("e1", provenance={"annotator": "claude_silver", "pass": 1, "origin": "silver"})
    gold_dir = tmp_path / "golden_dataset" / "annotations"
    p = gold_dir / "doc_900.ann.json"
    write_canonical(p, _ann([ent], annotator="claude_silver", mode="silver", silver=silver))
    r = validate_file(p, text_lookup=_lookup("Jane Example"))
    assert "silver_in_gold_folder" in {i.code for i in r.errors}


def test_policy_decisions():
    pol = load_policy()
    assert pol.decide("DATE").action == "KEEP"
    assert pol.decide("TFN").action == "REDACT" and pol.decide("TFN").token == "[TFN]"
    assert pol.decide("DATE_OF_BIRTH").strategy == "dob_age_preserving"
    assert pol.decide("ORGANIZATION", "statutory_body").action == "KEEP"
    assert pol.decide("ORGANIZATION", "employer").action == "SYNTHETIC"
    assert pol.decide("ORGANIZATION", "insurer").action == "REVIEW"
    assert pol.decide("LOCATION", attributes={"granularity": "state"}).action == "KEEP"
    assert pol.decide("LOCATION", attributes={"granularity": "suburb"}).action == "SYNTHETIC"
    assert pol.decide("MEDICARE", validator="medicare").strategy == "checksum_invalid"
    assert pol.decide("CLAIM_NUMBER").strategy == "format_preserving"
    assert pol.decide("PHONE", attributes={"number_class": "1300"}).action == "KEEP"
    assert pol.is_critical("PERSON", "plaintiff") and not pol.is_critical("PERSON", "examining_expert")
    assert pol.is_critical("MEDICARE")


def test_registry_unique():
    with pytest.raises(ValidationError):
        CanonicalRegistry.model_validate({"schema": "golden.canonical_registry", "matter_id": "m",
                                          "entities": [{"canonical_id": "m/p1", "entity_type": "PERSON"},
                                                       {"canonical_id": "m/p1", "entity_type": "PERSON"}]})
