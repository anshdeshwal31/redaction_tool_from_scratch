"""Generate JSON Schemas from the pydantic models into schemas/ (plan §3.1 principle 4)."""

from __future__ import annotations

from pathlib import Path

from ..core.canonical import write_canonical
from ..paths import schemas_dir
from .models import SCHEMA_MODELS, SCHEMA_VERSION


def export_schemas(out_dir: Path | None = None) -> dict[str, str]:
    out_dir = out_dir or schemas_dir()
    written: dict[str, str] = {}
    from ..output.export import VERSION as OUT_VERSION
    from ..output.models import PseudonymizedDocument
    models = {name: (model, SCHEMA_VERSION) for name, model in SCHEMA_MODELS.items()}
    models["redaction.pseudonymized_document"] = (PseudonymizedDocument, OUT_VERSION)
    for name, (model, version) in sorted(models.items()):
        schema = model.model_json_schema(by_alias=True, mode="validation")
        schema["$id"] = f"{name}.v{version}"
        path = out_dir / f"{name}.v{version}.schema.json"
        written[path.name] = write_canonical(path, schema)
    return written
