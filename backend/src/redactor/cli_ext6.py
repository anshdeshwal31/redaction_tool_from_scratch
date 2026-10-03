"""CLI for G1 onwards: gate calibration and review queue. Aggregate output only."""

from __future__ import annotations

import json


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False))


def cmd_gate_calibrate(args) -> int:
    from .gate.calibrate import calibrate
    _print(calibrate(args.t_word))
    return 0


def register(sub) -> None:
    gate = sub.add_parser("gate").add_subparsers(dest="command", required=True)
    c = gate.add_parser("calibrate", help="provisional threshold sweep on silver dev labels (aggregates only)")
    c.add_argument("--t-word", type=float, default=60.0)
    c.set_defaults(func=cmd_gate_calibrate)
    try:
        from . import cli_ext7
        cli_ext7.register(sub)
    except ImportError:
        pass
