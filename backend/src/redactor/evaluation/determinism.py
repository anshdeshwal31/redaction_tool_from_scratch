"""Determinism harness (plan §4.7): N in-process runs plus M fresh-process runs with different
PYTHONHASHSEED values. Compared: span sets, emitted order, types and (for later stages) canonical
IDs, replacements, gate decisions and output hashes, all through one canonical digest per run.
The structured diff of the first divergence carries positions and types only, never text.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any, Callable, Mapping, Sequence

from ..core.canonical import canonical_json, sha256_hex
from ..core.types import EntitySpan

DEFAULT_FRESH_SEEDS = (11, 23, 37, 41, 53)


def spans_payload(results: Mapping[str, Sequence[EntitySpan]]) -> list[dict[str, Any]]:
    """Flat list in emitted order (documents sorted by ID; within a document, as emitted)."""
    out = []
    for doc in sorted(results):
        for i, s in enumerate(results[doc]):
            d = s.to_dict(include_text=True)
            d["_i"] = i
            out.append(d)
    return out


def digest(payload: Any) -> str:
    return sha256_hex(canonical_json(payload))


def _pos(d: Mapping[str, Any]) -> tuple:
    return (d["document_id"], d.get("page") or -1, d.get("field") or "", d["start"], d["end"])


def first_divergence(ref: Sequence[Mapping[str, Any]], other: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """Text-free structured diff: added / removed / changed spans and whether only the order differs."""
    if canonical_json(list(ref)) == canonical_json(list(other)):
        return None
    strip = (lambda d: {k: v for k, v in d.items() if k not in ("_i", "text")})
    ra = {_pos(d): strip(d) for d in ref}
    rb = {_pos(d): strip(d) for d in other}
    added = sorted(k for k in rb if k not in ra)
    removed = sorted(k for k in ra if k not in rb)
    changed = []
    for k in sorted(set(ra) & set(rb)):
        if canonical_json(ra[k]) != canonical_json(rb[k]):
            fields = sorted(f for f in set(ra[k]) | set(rb[k]) if canonical_json(ra[k].get(f)) != canonical_json(rb[k].get(f)))
            changed.append({"position": list(k), "fields": fields,
                            "type": [ra[k].get("entity_type"), rb[k].get("entity_type")]})
    first_index = next((i for i, (x, y) in enumerate(zip(ref, other)) if canonical_json(x) != canonical_json(y)),
                       min(len(ref), len(other)))
    return {"first_index": first_index, "added": [list(k) for k in added[:20]], "removed": [list(k) for k in removed[:20]],
            "changed": changed[:20], "counts": {"added": len(added), "removed": len(removed), "changed": len(changed)},
            "order_only": not added and not removed and not changed}


def in_process(fn: Callable[[], Any], runs: int) -> list[tuple[str, Any]]:
    out = []
    for _ in range(runs):
        payload = fn()
        out.append((digest(payload), payload))
    return out


def fresh_process(argv: Sequence[str], seeds: Sequence[int] = DEFAULT_FRESH_SEEDS, *, timeout: int = 3600,
                  cwd: str | None = None) -> list[dict[str, Any]]:
    """Run `python -m redactor.cli <argv>` once per seed; the command prints one JSON object with `digest`."""
    out = []
    for seed in seeds:
        env = dict(os.environ)
        env.update({"PYTHONHASHSEED": str(seed), "OMP_THREAD_LIMIT": "1", "ORT_NUM_THREADS": "1",
                    "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"})
        r = subprocess.run([sys.executable, "-m", "redactor.cli", *argv], capture_output=True, text=True,
                           env=env, timeout=timeout, cwd=cwd)
        rec: dict[str, Any] = {"seed": seed, "exit": r.returncode}
        if r.returncode == 0:
            try:
                rec.update(json.loads(r.stdout.strip().splitlines()[-1]))
            except (ValueError, IndexError):
                rec["error"] = "unparseable_output"
        else:
            rec["error"] = "nonzero_exit"
        out.append(rec)
    return out


def summarize(reference: str, in_proc: Sequence[tuple[str, Any]], fresh: Sequence[Mapping[str, Any]],
              ref_payload: Any = None) -> dict[str, Any]:
    in_same = sum(1 for d, _ in in_proc if d == reference)
    fr_same = sum(1 for r in fresh if r.get("digest") == reference)
    diff = None
    if ref_payload is not None:
        for d, p in in_proc:
            if d != reference:
                diff = first_divergence(ref_payload, p)
                break
    total = len(in_proc) + len(fresh)
    return {"reference_digest": reference[:16], "in_process": {"runs": len(in_proc), "identical": in_same},
            "fresh_process": {"runs": len(fresh), "identical": fr_same, "seeds": [r["seed"] for r in fresh],
                              "errors": sum(1 for r in fresh if "error" in r)},
            "total_runs": total, "identical": in_same + fr_same, "all_identical": in_same + fr_same == total,
            "first_divergence": diff}
