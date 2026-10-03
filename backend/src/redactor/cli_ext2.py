"""CLI commands for F3 onwards. Every command prints aggregates only."""

from __future__ import annotations

import json


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False))


def cmd_extract(args) -> int:
    from .dataset.register import load_manifest
    from .extraction.pipeline import extract_document
    docs = [d.document_id for d in load_manifest().documents] if args.doc == "all" else [args.doc]
    out = {doc: extract_document(doc, force=args.force) for doc in docs}
    _print(out)
    return 0


def register(sub) -> None:
    ex = sub.add_parser("extract", help="extract every page (text layer / OCR / hybrid); aggregate output")
    ex.add_argument("--doc", default="all")
    ex.add_argument("--force", action="store_true")
    ex.set_defaults(func=cmd_extract)

    try:
        from . import cli_ext3
        cli_ext3.register(sub)
    except ImportError:
        pass
