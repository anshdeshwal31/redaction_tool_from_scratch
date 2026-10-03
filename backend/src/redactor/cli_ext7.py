"""CLI for the production path: `pipeline run` (gate) and, from X1 on, export and re-identification."""

from __future__ import annotations

import json


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False))


def cmd_pipeline_run(args) -> int:
    from .pipeline import run_matter
    _print(run_matter(args.matter))
    return 0


def register(sub) -> None:
    pl = sub.add_parser("pipeline").add_subparsers(dest="command", required=True)
    r = pl.add_parser("run", help="extraction -> detection -> replacement -> release gate for one matter "
                                  "(vault passphrase from REDACTOR_VAULT_PASSPHRASE)")
    r.add_argument("--matter", required=True)
    r.set_defaults(func=cmd_pipeline_run)
    try:
        from . import cli_ext8
        cli_ext8.register(sub)
    except ImportError:
        pass
