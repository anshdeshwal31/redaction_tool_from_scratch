"""Registration of the later milestones' CLI commands (kept separate so cli.py stays small).

Every command prints aggregates only (counts, rates, hashes, IDs).
"""

from __future__ import annotations

import json


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False))


def cmd_register(args) -> int:
    from .dataset.localrepo import ensure_local_repo
    from .dataset.register import register
    result = register()
    out = result.summary()
    out["golden_repo"] = ensure_local_repo()
    _print(out)
    return 0


def cmd_verify_hashes(args) -> int:
    from .dataset.register import verify_hashes
    out = verify_hashes()
    _print(out)
    return 0 if out["ok"] else 1


def cmd_classify(args) -> int:
    from .ingest.metadata import classify_all
    _print(classify_all())
    return 0


def cmd_sample(args) -> int:
    from .dataset.sample import draw_for_dataset
    out = draw_for_dataset(force=args.force)
    _print(out)
    return 0 if out["ok"] else 1


def register(sub) -> None:
    dataset = next(a for a in sub.choices["dataset"]._actions if a.dest == "command")
    dataset.add_parser("register", help="safe IDs, hashes, read-only copies, manifest").set_defaults(func=cmd_register)
    dataset.add_parser("verify-hashes", help="checksum guard for originals and copies").set_defaults(func=cmd_verify_hashes)
    sp = dataset.add_parser("sample", help="draw the seeded silver verification sample into the manifest (once)")
    sp.add_argument("--force", action="store_true", help="redraw (only before any verification has started)")
    sp.set_defaults(func=cmd_sample)

    ingest = sub.add_parser("ingest").add_subparsers(dest="command", required=True)
    ingest.add_parser("classify", help="page classifier + metadata writer").set_defaults(func=cmd_classify)

    try:
        from . import cli_ext2
        cli_ext2.register(sub)
    except ImportError:
        pass
