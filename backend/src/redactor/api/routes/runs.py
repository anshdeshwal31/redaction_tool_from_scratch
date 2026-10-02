"""/experiments, /runs and /jobs. Launched runs execute in a separate worker process (plan §4.10).
Public run files are served as stored; private files only to this machine's UI."""

from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from ...core.canonical import read_json, sha256_hex
from ...paths import REPO_ROOT, experiments_dir, runs_dir

router = APIRouter()
_JOBS: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()


def _run_dir(run_id: str) -> Path:
    if "/" in run_id or "\\" in run_id or ".." in run_id:
        raise HTTPException(400, "bad run id")
    p = runs_dir() / run_id
    if not p.is_dir():
        raise HTTPException(404, "unknown run")
    return p


@router.get("/experiments")
def experiments() -> list[dict[str, Any]]:
    out = []
    for p in sorted(experiments_dir().glob("*.yaml")):
        out.append({"name": p.stem, "file": f"experiments/{p.name}"})
    return out


@router.post("/experiments/{name}/launch")
def launch(name: str, allow_test: bool = False) -> dict[str, Any]:
    path = experiments_dir() / f"{name}.yaml"
    if not path.exists():
        raise HTTPException(404, "unknown experiment")
    job_id = sha256_hex(f"{name}|{len(_JOBS)}")[:12]
    cmd = [sys.executable, "-m", "redactor.cli", "run", str(path)] + (["--allow-test", "--split", "dev", "--split", "test"] if allow_test else [])
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, cwd=str(REPO_ROOT / "backend"))
    with _LOCK:
        _JOBS[job_id] = {"job_id": job_id, "experiment": name, "state": "running", "proc": proc}
    return {"job_id": job_id, "state": "running"}


@router.get("/jobs/{job_id}")
def job(job_id: str) -> dict[str, Any]:
    j = _JOBS.get(job_id)
    if j is None:
        raise HTTPException(404, "unknown job")
    proc = j["proc"]
    if proc.poll() is not None and j["state"] == "running":
        out = proc.stdout.read() if proc.stdout else ""
        j["state"] = "done" if proc.returncode == 0 else "failed"
        try:
            import json
            j["summary"] = json.loads(out)
        except ValueError:
            j["summary"] = None
    return {k: v for k, v in j.items() if k != "proc"}


@router.get("/runs")
def runs() -> list[dict[str, Any]]:
    out = []
    root = runs_dir()
    if not root.exists():
        return out
    for p in sorted(root.iterdir()):
        res = p / "public" / "results.json"
        if not res.exists():
            continue
        r = read_json(res)
        splits = {}
        for s, v in r.get("splits", {}).items():
            m = v["detection"]["overlap_any"]["micro"]
            splits[s] = {"gold_status": v["gold_status"], "precision": m["precision"], "recall": m["recall"], "f1": m["f1"],
                         "strict_f1": v["detection"]["strict"]["micro"]["f1"],
                         "protection_recall": v["protection"]["pessimistic"]["mention_recall"],
                         "residual": v["protection"]["pessimistic"]["residual_mentions"],
                         "gold_protect": v["protection"]["pessimistic"]["gold_protect"]}
        for sname, v in r.get("splits", {}).items():
            conf = v.get("ocr", {}).get("mean_word_confidence", {})
            vals = [x for x in conf.values() if isinstance(x, (int, float))]
            splits[sname]["ocr_mean_conf"] = round(sum(vals) / len(vals), 2) if vals else None
            rp = (r.get("replacement") or {}).get(sname) or {}
            if "residual" in rp:
                splits[sname].update(
                    residual_after_replacement=rp["residual"]["pessimistic"], keep_rate=rp["preservation"]["keep_rate"],
                    non_pii_char_preservation=rp["preservation"]["non_pii_char_preservation"],
                    alias_consistency=rp["consistency"]["alias_consistency"], grouping_b3_f1=rp["consistency"]["grouping_b3"]["f1"])
            g = (r.get("gate") or {}).get(sname) or {}
            if "gate_recall" in g:
                splits[sname].update(gate_recall=g["gate_recall"], unsafe_passes=g["unsafe_passes"], review_load=g["review_load"])
            o = (r.get("outputs") or {}).get(sname) or {}
            if "roundtrip" in o:
                splits[sname].update(roundtrip_rate=o["roundtrip"]["roundtrip_rate"],
                                     wrong_restoration_rate=o["downstream"]["wrong_restoration_rate"])
        det = r.get("determinism", {})
        rdet = r.get("replacement_determinism") or {}
        out.append({"run_id": r["run_id"], "experiment_id": r["experiment_id"], "detector": r["detector"], "splits": splits,
                    "documents": sorted({d for v in r.get("splits", {}).values() for d in v.get("documents", [])}),
                    "determinism": f"{det.get('identical', 0)}/{det.get('total_runs', 0)}",
                    "replacement_determinism": f"{rdet['identical']}/{rdet['total_runs']}" if rdet else None,
                    "leak_test_passed": (r.get("leak_test") or {}).get("passed"),
                    "runtime_ms_per_page_p50": (r.get("runtime") or {}).get("ms_per_page_p50")})
    return out


@router.get("/runs/{run_id}")
def run(run_id: str) -> dict[str, Any]:
    return read_json(_run_dir(run_id) / "public" / "results.json")


@router.get("/runs/{run_id}/report", response_class=PlainTextResponse)
def report(run_id: str) -> str:
    return (_run_dir(run_id) / "public" / "report.md").read_text(encoding="utf-8")


@router.get("/runs/{run_id}/private/mentions")
def private_mentions(run_id: str) -> dict[str, Any]:
    """Local UI only (the API refuses non-loopback clients): gold mentions with their outcomes."""
    p = _run_dir(run_id) / "private" / "mentions.json"
    if not p.exists():
        raise HTTPException(404, "no private data")
    return read_json(p)


@router.get("/runs/{run_id}/private/predictions/{doc}")
def private_predictions(run_id: str, doc: str) -> list[dict[str, Any]]:
    p = _run_dir(run_id) / "private" / "predictions.json"
    if not p.exists():
        raise HTTPException(404, "no private data")
    return read_json(p).get(doc, [])


@router.get("/runs/{run_id}/private/overlay/{doc}/{page}")
def private_overlay(run_id: str, doc: str, page: int) -> dict[str, Any]:
    """Gold vs prediction boxes for one page (U1), local only: gold regions with their outcome and
    match status, predicted boxes with their type and whether they overlap a gold mention."""
    from ...evaluation.gold import load_gold
    from ...ingest.metadata import load_metadata
    run_dir = _run_dir(run_id)
    pp = run_dir / "private" / "predictions.json"
    mp = run_dir / "private" / "mentions.json"
    if not pp.exists() or not mp.exists():
        raise HTTPException(404, "no private data")
    meta = load_metadata(doc)
    pg = next((p for p in meta.pages if p.page == page), None) if meta else None
    if pg is None:
        raise HTTPException(404, "unknown page")
    outcomes = {}
    for split in read_json(mp).values():
        for m in split.get("mentions", []):
            if m["document_id"] == doc:
                outcomes[m["id"]] = m
    gold = []
    g = load_gold(doc)
    if g is not None:
        for e in g.annotation.entities:
            if e.page != page:
                continue
            m = outcomes.get(e.entity_id, {})
            gold.append({"id": e.entity_id, "type": e.entity_type, "action": e.action, "text": e.text,
                         "boxes": [{"x0": r.x0, "y0": r.y0, "x1": r.x1, "y1": r.y1} for r in e.regions],
                         "detected": m.get("detected"), "protected": m.get("protected"), "outcome": m.get("outcome")})
    preds = [{"type": s["entity_type"], "native": s.get("native_type"), "text": s["text"], "detector": s["detector"],
              "boxes": s.get("bboxes", []), "confidence": s.get("confidence")}
             for s in read_json(pp).get(doc, []) if s.get("page") == page]
    return {"document_id": doc, "page": page, "width_pt": pg.width_pt, "height_pt": pg.height_pt, "gold": gold, "predictions": preds}
