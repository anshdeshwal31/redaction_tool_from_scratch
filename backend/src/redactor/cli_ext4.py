"""CLI for F5 onwards: experiments, IAA, synthetic fixtures. Aggregate output only."""

from __future__ import annotations

import json
import sys


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False))


def cmd_run(args) -> int:
    from .experiments.runner import ExperimentError, run
    try:
        out = run(args.experiment, splits=args.split or None, allow_test=args.allow_test,
                  in_process=args.in_process, fresh=args.fresh)
    except ExperimentError as exc:
        _print({"ok": False, "error": str(exc)})
        return 2
    _print(out)
    return 0 if out["ok"] else 1


def cmd_digest(args) -> int:
    from .experiments.runner import digest_main
    out = digest_main(args.experiment, args.split or ["dev"], args.allow_test)
    sys.stdout.write(json.dumps(out, sort_keys=True) + "\n")
    return 0


def cmd_synth_fixtures(args) -> int:
    from .paths import fixtures_dir
    from .synth.fixtures import build_all
    _print(build_all(fixtures_dir()))
    return 0


def register(sub) -> None:
    r = sub.add_parser("run", help="run an experiment YAML (detection, evaluation, determinism, reports, leak test)")
    r.add_argument("experiment")
    r.add_argument("--split", action="append", choices=("dev", "test"))
    r.add_argument("--allow-test", action="store_true", help="score the held-out test split (never for tuning)")
    r.add_argument("--in-process", type=int, default=None)
    r.add_argument("--fresh", type=int, default=None)
    r.set_defaults(func=cmd_run)
    ex = sub.add_parser("experiment").add_subparsers(dest="command", required=True)
    d = ex.add_parser("digest", help="(internal) detection digest for fresh-process determinism runs")
    d.add_argument("experiment")
    d.add_argument("--split", action="append", choices=("dev", "test"))
    d.add_argument("--allow-test", action="store_true")
    d.set_defaults(func=cmd_digest)
    syn = sub.add_parser("synth").add_subparsers(dest="command", required=True)
    syn.add_parser("fixtures", help="build fixtures/synthetic/ (PDFs + exact ground truth)").set_defaults(func=cmd_synth_fixtures)
    try:
        from . import cli_ext5
        cli_ext5.register(sub)
    except ImportError:
        pass
