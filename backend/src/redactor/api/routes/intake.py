"""/intake: document upload (plan §4.10, §11 U1). Local only (app middleware). The upload's filename is
never read or stored; responses carry IDs, counts and states only."""

from __future__ import annotations

import subprocess
import sys
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ...dataset import intake
from ...paths import REPO_ROOT

router = APIRouter()
_JOBS: dict[str, subprocess.Popen] = {}


@router.post("/intake/{matter}")
async def upload(matter: str, request: Request) -> dict[str, Any]:
    """Body: the raw PDF bytes (Content-Type: application/pdf)."""
    data = await request.body()
    try:
        rec = intake.add_pdf(data, matter)
    except intake.IntakeError as exc:
        raise HTTPException(422, str(exc))
    doc = rec["document_id"]
    if rec["new"] or intake.status(doc)["state"] != "extracted":
        if doc not in _JOBS or _JOBS[doc].poll() is not None:
            _JOBS[doc] = subprocess.Popen([sys.executable, "-m", "redactor.cli", "intake", "process", "--doc", doc],
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(REPO_ROOT / "backend"))
    return {**rec, **intake.status(doc)}


@router.get("/intake")
def intake_list() -> list[dict[str, Any]]:
    from ...dataset.register import load_manifest
    out = []
    for d in load_manifest().documents:
        if d.split == "intake":
            st = intake.status(d.document_id)
            job = _JOBS.get(d.document_id)
            st["job"] = None if job is None else ("running" if job.poll() is None else ("done" if job.returncode == 0 else "failed"))
            out.append({"matter_id": d.matter_id, **st})
    return out
