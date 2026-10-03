"""CLI commands for S1 onwards. Every command prints aggregates only."""

from __future__ import annotations

import json


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False))


def cmd_silver_packets(args) -> int:
    from .dataset.register import load_manifest
    from .dataset.silver import make_packets
    docs = [d.document_id for d in load_manifest().documents] if args.doc == "all" else [args.doc]
    _print([make_packets(d) for d in docs])
    return 0


def cmd_silver_build(args) -> int:
    from .dataset.silver import build_silver
    out = build_silver(args.doc, model_id=args.model_id, run_date=args.run_date)
    out.pop("error_detail", None) if not args.verbose else None
    _print(out)
    return 0 if out.get("ok") else 1


def cmd_silver_registry(args) -> int:
    from .dataset.silver import build_registry
    _print(build_registry(model_id=args.model_id, run_date=args.run_date))
    return 0


def cmd_silver_rotview(args) -> int:
    from .dataset.silver import make_rotated_views
    pages: list[int] = []
    for part in args.pages.split(","):
        a, _, b = part.partition("-")
        pages += list(range(int(a), int(b or a) + 1))
    _print(make_rotated_views(args.doc, sorted(set(pages)), args.rotate, args.psm))
    return 0


def register(sub) -> None:
    silver = sub.add_parser("silver", help="S1 silver-pass tooling (EX-001)").add_subparsers(dest="command", required=True)
    p = silver.add_parser("packets")
    p.add_argument("--doc", default="all")
    p.set_defaults(func=cmd_silver_packets)
    rv = silver.add_parser("rotview", help="aux OCR of pages scanned sideways (annotator view, never an anchor)")
    rv.add_argument("--doc", required=True)
    rv.add_argument("--pages", required=True, help="e.g. 13-23,25")
    rv.add_argument("--rotate", type=int, choices=(90, 270), required=True, help="clockwise degrees to upright")
    rv.add_argument("--psm", type=int, choices=(3, 4, 6, 11, 12), default=None, help="page segmentation (default: reference)")
    rv.set_defaults(func=cmd_silver_rotview)
    b = silver.add_parser("build")
    b.add_argument("--doc", required=True)
    b.add_argument("--model-id", required=True)
    b.add_argument("--run-date", default=None)
    b.add_argument("--verbose", action="store_true", help="issue codes with entity IDs (never text)")
    b.set_defaults(func=cmd_silver_build)
    r = silver.add_parser("registry")
    r.add_argument("--model-id", required=True)
    r.add_argument("--run-date", default=None)
    r.set_defaults(func=cmd_silver_registry)

    try:
        from . import cli_ext4
        cli_ext4.register(sub)
    except ImportError:
        pass
