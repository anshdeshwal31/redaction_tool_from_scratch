"""Pydantic model of `redaction.pseudonymized_document` v0.1 (plan §4.12.1); the JSON Schema in schemas/ is
generated from it. `extra="forbid"` everywhere: a field that could carry original text cannot slip in."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Box(_M):
    x0: float
    y0: float
    x1: float
    y1: float


class Span(_M):
    span_id: str
    start: int
    end: int
    entity_type: str
    canonical_id: str | None
    action: Literal["SYNTHETIC", "REDACT", "REVIEW", "KEEP"]
    bboxes: list[Box]


class PageGate(_M):
    status: str
    reasons: list[str]


class Page(_M):
    page: int
    content_kind: str
    text_source_id: str
    text: str
    gate: PageGate
    spans: list[Span]


class EditEntry(_M):
    edit_id: str
    page: int | None
    field: str | None = None
    span_id: str | None
    new_start: int
    new_end: int
    entity_type: str
    canonical_id: str | None
    action: str
    strategy: str | None
    rule_id: str


class DocGate(_M):
    status: str
    leak_scan: str


class PseudonymizedDocument(_M):
    schema_: Literal["redaction.pseudonymized_document"] = Field(alias="schema")
    schema_version: str
    document_id: str
    matter_id: str
    source_sha256: str
    pipeline: dict[str, object]
    pages: list[Page]
    fields: dict[str, str]
    edit_log: list[EditEntry]
    gate: DocGate
    output_sha256: str

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


def validate_document(doc: dict) -> bool:
    try:
        PseudonymizedDocument.model_validate(doc)
        return True
    except Exception:  # noqa: BLE001 - validity is the metric
        return False
