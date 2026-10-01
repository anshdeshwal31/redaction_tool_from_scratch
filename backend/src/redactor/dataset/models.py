"""Golden-dataset models (plan §3). The pydantic models are the source of truth; JSON Schemas
are generated from them into schemas/ (`redactor schema export`)."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..taxonomy import ACTIONS, ENTITY_TYPES

SCHEMA_VERSION = "0.1.0"

EntityType = Literal[ENTITY_TYPES]  # type: ignore[valid-type]
Action = Literal[ACTIONS]  # type: ignore[valid-type]
Annotator = Literal["A1", "A2", "claude_silver", "adjudicated"]
Mode = Literal["blind", "candidates_shown", "silver", "adjudicated"]
Flag = Literal["handwritten", "ocr_degraded", "split_line", "split_page", "partially_illegible", "quasi_identifier"]
Certainty = Literal["certain", "probable", "uncertain"]
Origin = Literal["manual", "preannotation_accepted", "preannotation_edited", "audit_added", "silver"]
Adjudication = Literal["agreed", "took_A1", "took_A2", "verified_silver", "merged", "added"]
FieldName = Literal["filename", "pdf.title", "pdf.author", "pdf.subject", "pdf.keywords", "xmp"]
ContentKind = Literal["born_digital", "scanned", "vector_outlined", "hybrid", "blank", "unknown"]
TextLayer = Literal["usable", "present_unusable", "absent"]
OcrScope = Literal["none", "raster_regions", "full_page"]
Split = Literal["dev", "test", "intake"]   # intake: uploaded for processing, never scored


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    def dump(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)


class Region(_Model):
    x0: float
    y0: float
    x1: float
    y1: float

    @model_validator(mode="after")
    def _ordered(self) -> "Region":
        if self.x1 < self.x0 or self.y1 < self.y0:
            raise ValueError("region must satisfy x0<=x1 and y0<=y1")
        return self


class TextAnchor(_Model):
    text_source_id: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)

    @model_validator(mode="after")
    def _ordered(self) -> "TextAnchor":
        if self.end <= self.start:
            raise ValueError("text_anchor end must be greater than start")
        return self


class Provenance(_Model):
    annotator: Annotator
    pass_: int = Field(alias="pass", ge=1)
    origin: Origin
    adjudication: Optional[Adjudication] = None
    verified_by: Optional[str] = None
    verified_date: Optional[str] = None


class Entity(_Model):
    entity_id: str
    canonical_id: Optional[str] = None
    entity_type: EntityType
    text: Optional[str] = None
    page: Optional[int] = Field(default=None, ge=1)
    field: Optional[FieldName] = None
    regions: list[Region] = Field(default_factory=list)
    text_anchor: Optional[TextAnchor] = None
    role: Optional[str] = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    action: Action
    action_source: Literal["policy", "override"] = "policy"
    override_reason: Optional[str] = None
    flags: list[Flag] = Field(default_factory=list)
    certainty: Certainty = "certain"
    notes: str = ""
    provenance: Provenance

    @model_validator(mode="after")
    def _location(self) -> "Entity":
        if (self.page is None) == (self.field is None):
            raise ValueError("an entity has exactly one of page or field")
        if self.action_source == "override" and not self.override_reason:
            raise ValueError("an override needs override_reason")
        return self


class Coverage(_Model):
    pages_complete: list[int] = Field(default_factory=list)
    pages_verified: list[int] = Field(default_factory=list)
    fields_complete: list[FieldName] = Field(default_factory=list)
    types_complete: list[EntityType] = Field(default_factory=list)


class IgnoreRegion(_Model):
    page: int = Field(ge=1)
    regions: list[Region]
    reason: str


class SilverInfo(_Model):
    model_id: str
    run_date: str
    guidelines_version: str
    prompt_sha256: str
    input_sha256s: dict[str, str]
    exception: Literal["EX-001"] = "EX-001"


class AnnotationSet(_Model):
    schema_: Literal["golden.annotation_set"] = Field(default="golden.annotation_set", alias="schema")
    schema_version: str = SCHEMA_VERSION
    document_id: str
    document_sha256: str
    annotator: Annotator
    pass_: int = Field(default=1, alias="pass", ge=1)
    mode: Mode
    revision: int = Field(default=1, ge=1)
    guidelines_version: str
    policy_ref: str
    coverage: Coverage = Field(default_factory=Coverage)
    entities: list[Entity] = Field(default_factory=list)
    ignore_regions: list[IgnoreRegion] = Field(default_factory=list)
    silver: Optional[SilverInfo] = None

    @model_validator(mode="after")
    def _silver_rules(self) -> "AnnotationSet":
        if self.annotator == "claude_silver":
            if self.mode != "silver" or self.silver is None:
                raise ValueError("claude_silver files need mode 'silver' and a silver block (EX-001)")
        elif self.silver is not None:
            raise ValueError("only claude_silver files carry a silver block")
        return self


class Signals(_Model):
    visible_text_chars: int
    invisible_text_chars: int
    image_count: int
    image_coverage: float
    image_dpi: list[int]
    vector_paths: int
    small_filled_paths: int = 0
    alnum_ratio: Optional[float]
    bad_char_ratio: Optional[float] = None
    largest_image_fraction: float = 0.0


class Classification(_Model):
    content_kind: ContentKind
    text_layer: TextLayer
    invisible_ocr_layer: bool = False
    ocr_required: bool
    ocr_scope: OcrScope
    classifier: str
    human_verified: bool = False


class RenderInfo(_Model):
    engine: str
    version: str
    dpi: int
    mode: Literal["gray", "rgb"] = "gray"


class ExtractionInfo(_Model):
    method: Literal["text_layer", "ocr", "hybrid"]
    text_source_id: str
    engine: str
    engine_version: str
    model_sha256: Optional[str] = None
    render: Optional[RenderInfo] = None


class PageMeta(_Model):
    page: int = Field(ge=1)
    width_pt: float
    height_pt: float
    rotation: int
    signals: Signals
    classification: Classification
    extraction: Optional[ExtractionInfo] = None


class PdfInfo(_Model):
    version: Optional[str]
    producer: Optional[str]
    creator: Optional[str] = None
    encrypted: bool
    metadata_fields_present: list[str]
    annotations: int
    form_fields: int
    embedded_files: int


class DocumentMetadata(_Model):
    schema_: Literal["golden.document_metadata"] = Field(default="golden.document_metadata", alias="schema")
    schema_version: str = SCHEMA_VERSION
    document_id: str
    matter_id: str
    split: Split
    original_filename: str
    source_path: str
    sha256: str
    size_bytes: int
    document_type: str
    pdf: PdfInfo
    page_count: int
    pages: list[PageMeta]


class RegistryEntity(_Model):
    canonical_id: str
    entity_type: EntityType
    role: Optional[str] = None
    gender: Optional[Literal["female", "male", "non_binary", "unknown"]] = None
    gender_evidence: Optional[str] = None
    date_of_birth: Optional[str] = None
    label: str = ""
    notes: str = ""


class CanonicalRegistry(_Model):
    schema_: Literal["golden.canonical_registry"] = Field(default="golden.canonical_registry", alias="schema")
    schema_version: str = SCHEMA_VERSION
    matter_id: str
    annotator: Annotator = "adjudicated"
    entities: list[RegistryEntity] = Field(default_factory=list)
    silver: Optional[SilverInfo] = None

    @field_validator("entities")
    @classmethod
    def _unique(cls, v: list[RegistryEntity]) -> list[RegistryEntity]:
        ids = [e.canonical_id for e in v]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate canonical_id in registry")
        return v


class TranscriptLine(_Model):
    text: str
    bbox: Region


class PageTranscript(_Model):
    schema_: Literal["golden.page_transcript"] = Field(default="golden.page_transcript", alias="schema")
    schema_version: str = SCHEMA_VERSION
    document_id: str
    page: int = Field(ge=1)
    lines: list[TranscriptLine]
    reading_order: Literal["top_down_left_right"] = "top_down_left_right"
    seeded_from: str
    verified_by: Optional[str] = None


class ManifestDocument(_Model):
    document_id: str
    sha256: str
    size_bytes: int
    matter_id: str
    split: Split
    registered_order: int
    source_name_sha256: str  # hash of the original filename: lets register recognise a file without storing its name here


class VerificationSample(_Model):
    seed: str
    drawn_before_verification: bool = True
    pages: dict[str, list[int]] = Field(default_factory=dict)


class Manifest(_Model):
    schema_: Literal["golden.manifest"] = Field(default="golden.manifest", alias="schema")
    schema_version: str = SCHEMA_VERSION
    dataset_version: str = "0.1.0"
    exceptions: list[str] = Field(default_factory=list)
    documents: list[ManifestDocument] = Field(default_factory=list)
    verification_sample: Optional[VerificationSample] = None
    files: dict[str, str] = Field(default_factory=dict)  # relative path -> sha256


SCHEMA_MODELS: dict[str, type[_Model]] = {
    "golden.annotation_set": AnnotationSet,
    "golden.document_metadata": DocumentMetadata,
    "golden.canonical_registry": CanonicalRegistry,
    "golden.page_transcript": PageTranscript,
    "golden.manifest": Manifest,
}
