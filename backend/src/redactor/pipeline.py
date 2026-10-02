"""Production path for one matter (plan §4.1 S0-S7): cached extraction -> detection -> linking/policy/
replacement -> release gate. State (page states, review items, leak-hit locations; never document
text) goes to data/review/<matter>/state.json; pseudonymised outputs to data/outputs/<matter>/ for the
export step (X1), which refuses while the gate is not clear."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import yaml

from .core.canonical import read_json, sha256_hex, write_canonical
from .paths import REPO_ROOT, data_dir


def outputs_dir(matter_id: str) -> Path:
    return data_dir() / "outputs" / matter_id


def state_path(matter_id: str) -> Path:
    return data_dir() / "review" / matter_id / "state.json"


def run_matter(matter_id: str, *, detector_cfg: Mapping[str, Any] | None = None, gate_config: str = "config/release_gate.v0.1.yaml",
               vault=None, document_ids: list[str] | None = None, attempt_fn=None) -> dict[str, Any]:
    from .dataset.register import load_manifest
    from .experiments.runner import load_document_text
    from .gate import queue
    from .gate.gate import default_attempt, run_gate
    from .ingest.metadata import load_metadata
    from .replacement.vault import Vault
    from .taxonomy import load_policy, load_taxonomy
    docs = sorted(document_ids or [d.document_id for d in load_manifest().documents if d.matter_id == matter_id])
    ref = {d: load_document_text(d) for d in docs}
    info = {}
    for d in docs:
        meta = load_metadata(d)
        info[d] = {p.page: {"class": p.classification.content_kind, "method": p.extraction.method if p.extraction else None}
                   for p in meta.pages}
    gate_cfg = yaml.safe_load((REPO_ROOT / gate_config).read_text(encoding="utf-8"))
    vault = vault or Vault.open(matter_id)
    det_cfg = detector_cfg or {"detectors": [{"name": "baseline"}]}
    policy = load_policy()
    res = run_gate(ref, info, detector_cfg=det_cfg, policy=policy,
                   taxonomy=load_taxonomy(), gate_cfg=gate_cfg, vault=vault, decisions=queue.load_decisions(matter_id),
                   attempt_fn=attempt_fn or default_attempt)
    out = outputs_dir(matter_id)
    hashes = {}
    for d, r in sorted(res.matter.documents.items()):
        payload = {"document_id": d, "pages": r.pages, "fields": r.fields, "edits": [e.to_dict() for e in r.edits],
                   "regions": [x for x in res.regions if x.get("document_id") == d],
                   "chosen_sources": {p: pe.text_source_id for p, pe in sorted(res.chosen[d].pages.items())},
                   "epoch": vault.epoch}
        hashes[d] = write_canonical(out / f"{d}.pseudonymized.json", payload)
    from .detectors.combiner import build as build_detector
    from .output.stage import stage
    meta = {"detectors": [build_detector(det_cfg).fingerprint()], "linker": "rule_based@0.1.0", "policy": policy.ref,
            "replacement": "surrogate@0.1.0", "gate": f"release_gate@{gate_cfg.get('version')}", "vault_epoch": vault.epoch,
            "config_hash": sha256_hex(repr((sorted(det_cfg.items(), key=str), gate_config)))}
    export_sha = stage(matter_id, res, vault, info, meta, out)
    state = {"schema": "redactor.gate_state", "export_sha256": export_sha, "matter_id": matter_id, "documents": docs,
             "pages": [{"document_id": p.document_id, "page": p.page, "page_class": p.page_class, "state": p.state,
                        "reasons": p.reasons, "chosen_source": p.chosen_source, "rescued_by": p.rescued_by,
                        "attempts": p.attempts} for p in res.pages],
             "items": res.items, "hits": [h.to_public() for h in res.hits], "export_allowed": res.export_allowed,
             "outputs_sha256": hashes, "summary": res.summary(), "epoch": vault.epoch,
             "stale_exports": _stale(matter_id, vault.epoch),
             "decisions_sha256": sha256_hex(repr(sorted((k, v["decision"]) for k, v in queue.latest(queue.load_decisions(matter_id)).items())))}
    write_canonical(state_path(matter_id), state)
    vault.commit()
    return {"matter_id": matter_id, **res.summary()}


def load_state(matter_id: str) -> dict[str, Any] | None:
    p = state_path(matter_id)
    return read_json(p) if p.exists() else None


def _stale(matter_id: str, epoch: int) -> list[str]:
    from .output.exporter import export_status
    return export_status(matter_id, epoch)["stale"]
