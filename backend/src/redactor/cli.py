"""Command-line entry point: `redactor <group> <command>`.

Output rule (plan §2.2): every command prints aggregates only (counts, rates, hashes, IDs).
No command prints document text; there is no flag in this CLI that does.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .security import netguard


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False))


# ---------------------------------------------------------------- schema / dataset
def cmd_schema_export(args) -> int:
    from .dataset.schema_export import export_schemas
    written = export_schemas()
    _print({"schemas_written": len(written), "files": {k: v[:12] for k, v in written.items()}})
    return 0


def cmd_dataset_validate(args) -> int:
    from .core.canonical import read_json
    from .dataset.models import AnnotationSet, CanonicalRegistry
    from .dataset.validate import Report, check_registry_mentions, summarize, validate_file
    from .paths import golden_dir

    paths: list[Path] = [Path(p) for p in args.paths]
    registry = None
    if args.registry:
        registry = CanonicalRegistry.model_validate(read_json(args.registry))
    if args.all:
        root = golden_dir()
        paths += sorted(root.glob("annotations/*.ann.json"))
        paths += sorted(root.glob("annotations_raw/*/*.ann.json"))
        paths += sorted(root.glob("registry/*.json"))
        paths += sorted(root.glob("metadata/*.meta.json"))
    if args.silver:
        silver_root = golden_dir() / "annotations_raw" / "claude_silver"
        paths += sorted(silver_root.glob("*.ann.json"))
        reg_path = silver_root / "registry" / "matter_001.entities.json"
        if reg_path.exists():
            paths.append(reg_path)
            registry = registry or CanonicalRegistry.model_validate(read_json(reg_path))
    reports = [validate_file(p, registry=registry) for p in paths]
    if registry is not None:
        sets = []
        for p in paths:
            raw = read_json(p)
            if isinstance(raw, dict) and raw.get("schema") == "golden.annotation_set":
                try:
                    sets.append(AnnotationSet.model_validate(raw))
                except Exception:  # noqa: BLE001 - already reported by validate_file
                    pass
        if sets:
            r = Report(path="<registry mentions>")
            check_registry_mentions(registry, sets, r)
            reports.append(r)
    out = summarize(reports)
    if args.verbose:
        out["reports"] = [
            {"file": Path(r.path).name, "schema": r.schema, "ok": r.ok, "counts": r.counts,
             "issues": [i.as_dict() for i in r.issues]}
            for r in reports
        ]
    _print(out)
    return 0 if all(r.ok for r in reports) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="redactor", description=__doc__)
    sub = parser.add_subparsers(dest="group", required=True)

    schema = sub.add_parser("schema").add_subparsers(dest="command", required=True)
    schema.add_parser("export", help="write JSON Schemas into schemas/").set_defaults(func=cmd_schema_export)

    dataset = sub.add_parser("dataset").add_subparsers(dest="command", required=True)
    v = dataset.add_parser("validate", help="validate golden-dataset files (aggregate output)")
    v.add_argument("paths", nargs="*")
    v.add_argument("--all", action="store_true", help="every file under golden_dataset/")
    v.add_argument("--silver", action="store_true", help="the claude_silver files and silver registry")
    v.add_argument("--registry", help="canonical registry to check references against")
    v.add_argument("--verbose", action="store_true", help="per-file issue codes and entity IDs (never text)")
    v.set_defaults(func=cmd_dataset_validate)

    from . import cli_ext
    cli_ext.register(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    netguard.install()
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
