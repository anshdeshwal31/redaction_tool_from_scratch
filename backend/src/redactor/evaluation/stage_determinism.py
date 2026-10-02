"""Stage-isolated determinism on synthetic data (plan §4.7): (c) linking and replacement, (d) gate decisions,
(e) output rendering (JSON, text, Markdown, PDF bytes and rasterised pages) and (f) re-identification.
Each stage yields a SHA-256 digest; the harness compares digests in-process and across fresh processes with
different PYTHONHASHSEED values. Detection (b) and end-to-end (g) are covered by every experiment run;
OCR including every retry rung (a) by `tests/test_stage_determinism.py` (needs the pinned Tesseract image).
Synthetic only: no document of the dataset is touched.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any

from ..core.canonical import canonical_json, sha256_hex

SEEDS = (11, 23, 37)


def synthetic_stage_digests() -> dict[str, str]:
    import yaml
    from ..core.types import DocumentText
    from ..detectors.base import run_detector
    from ..detectors.combiner import build as build_detector
    from ..extraction.text_layer import extract
    from ..gate.gate import run_gate
    from ..output import pdf as pdfmod
    from ..output.export import build, opaque_ids
    from ..output.render import render_markdown, render_text
    from ..paths import config_dir, fixtures_dir
    from ..reidentify.core import build_index, reidentify
    from ..replacement.engine import output_digest, pseudonymize
    from ..replacement.pools import load_pools
    from ..replacement.vault import Vault
    from ..taxonomy import load_policy, load_taxonomy

    policy, tax = load_policy(), load_taxonomy()
    pdf = fixtures_dir() / "syn_born_digital.pdf"
    doc = DocumentText("doc_1", {1: extract("doc_1", pdf, 1)}, {})
    det = build_detector({"detectors": [{"name": "baseline"}]})
    spans = run_detector(det, [doc])
    vault = Vault.ephemeral()
    matter = pseudonymize([doc], spans, policy=policy, taxonomy=tax, vault=vault)
    out: dict[str, str] = {"replacement": output_digest(matter)}

    gate_cfg = yaml.safe_load((config_dir() / "release_gate.v0.1.yaml").read_text(encoding="utf-8"))
    g = run_gate({"doc_1": doc}, {"doc_1": {1: {"class": "born_digital", "method": "text_layer"}}},
                 detector_cfg={"detectors": [{"name": "baseline"}]}, policy=policy, taxonomy=tax, gate_cfg=gate_cfg,
                 vault=Vault.ephemeral(), attempt_fn=lambda *a: [])
    out["gate"] = g.digest()

    r = matter.documents["doc_1"]
    ids = opaque_ids([e.group_id for e in r.edits if e.group_id], "matter_t")
    js, priv = build("doc_1", "matter_t", "", doc, r, ids=ids, matter_key=vault.matter_key, page_classes={1: "born_digital"},
                     gate_pages=None, leak_hits=0, pipeline={"stage_determinism": True})
    out["json"] = sha256_hex(canonical_json(js))
    out["edit_log_private"] = sha256_hex(canonical_json(priv))
    out["text"] = sha256_hex(render_text(js))
    out["markdown"] = sha256_hex(render_markdown(js))
    pdf_bytes, images, _ = pdfmod.burn_document(pdf, js, [], dpi=150)
    out["pdf_bytes"] = sha256_hex(pdf_bytes.hex())
    out["pdf_raster"] = sha256_hex("".join(sha256_hex(im.tobytes().hex()) for im in images))

    pools = load_pools()
    idx = build_index(vault, pool_names=set(pools.surnames) | set(pools.female) | set(pools.male) | set(pools.unisex))
    restored, rep = reidentify(r.pages[1], idx)
    out["reidentify"] = sha256_hex(canonical_json({"text": restored, "report": rep.to_dict()}))
    return dict(sorted(out.items()))


def run(in_process: int = 3, seeds=SEEDS) -> dict[str, Any]:
    """Digest identity per stage: `in_process` repeats plus one fresh process per seed."""
    ref = synthetic_stage_digests()
    runs = [synthetic_stage_digests() for _ in range(max(0, in_process - 1))]
    fresh = []
    code = ("import json; from redactor.evaluation.stage_determinism import synthetic_stage_digests as f; "
            "print(json.dumps(f()))")
    for seed in seeds:
        env = {**os.environ, "PYTHONHASHSEED": str(seed), "OMP_THREAD_LIMIT": "1"}
        p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
        fresh.append(json.loads(p.stdout.strip().splitlines()[-1]) if p.returncode == 0 else {"error": p.returncode})
    stages = {}
    for k in ref:
        same_in = 1 + sum(1 for r in runs if r.get(k) == ref[k])
        same_fresh = sum(1 for r in fresh if r.get(k) == ref[k])
        stages[k] = {"in_process": f"{same_in}/{1 + len(runs)}", "fresh_process": f"{same_fresh}/{len(fresh)}",
                     "identical": same_in == 1 + len(runs) and same_fresh == len(fresh), "digest": ref[k][:16]}
    return {"seeds": list(seeds), "stages": stages, "all_identical": all(v["identical"] for v in stages.values())}
