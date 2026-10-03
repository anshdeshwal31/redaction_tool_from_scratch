"""Experiment runner (plan §4.4): detection on frozen extraction, evaluation per split, determinism,
runtime, run manifest, public/private outputs and the leak test.

The test split is scored only with `allow_test` (it is never used for rule tuning, plan §8).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from ..core.canonical import canonical_json, read_json, sha256_file, sha256_hex
from ..core.types import DocumentText, EntitySpan
from ..detectors.base import run_detector
from ..detectors.combiner import build as build_detector
from ..evaluation import determinism as det
from ..evaluation.evaluate import evaluate_split
from ..evaluation.gold import GoldDocument, load_gold
from ..extraction import cache
from ..paths import REPO_ROOT, golden_dir, runs_dir
from ..reporting import leak
from ..reporting.private import write_private
from ..reporting.public import report_md, write_public
from ..taxonomy import load_policy, load_taxonomy
from .manifest import run_manifest


class ExperimentError(ValueError):
    pass


@dataclass(frozen=True)
class Experiment:
    path: Path
    raw: Mapping[str, Any]

    @property
    def experiment_id(self) -> str:
        return str(self.raw["experiment_id"])

    @property
    def config_sha256(self) -> str:
        return sha256_hex(canonical_json(self.raw))

    def splits(self) -> list[str]:
        s = (self.raw.get("dataset") or {}).get("split") or ["dev"]
        return [s] if isinstance(s, str) else list(s)

    def policy_path(self) -> str | None:
        p = self.raw.get("policy")
        return str(REPO_ROOT / p) if p else None

    def determinism(self) -> tuple[int, int]:
        d = self.raw.get("determinism") or {}
        return int(d.get("in_process_runs", 20)), int(d.get("fresh_process_runs", 5))


def load_experiment(path: str | Path) -> Experiment:
    p = Path(path)
    if not p.is_absolute() and not p.exists():
        p = REPO_ROOT / p
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    for key in ("experiment_id", "detectors"):
        if key not in raw:
            raise ExperimentError(f"experiment is missing '{key}'")
    for d in raw["detectors"]:
        cfg = d.get("config")
        if isinstance(cfg, str):  # a config file path: resolved relative to the repository
            d["config"] = {"path": str(REPO_ROOT / cfg)}
    return Experiment(p, raw)


def dataset_documents(splits: Sequence[str]) -> list[tuple[str, str]]:
    from ..dataset.register import load_manifest
    m = load_manifest()
    return sorted((d.document_id, d.split) for d in m.documents if d.split in splits)


def load_document_text(document_id: str) -> DocumentText:
    pages = cache.load_reference_pages(document_id)
    if not pages:
        raise ExperimentError(f"no cached extraction for {document_id}: run `redactor extract` first")
    return DocumentText(document_id, dict(sorted(pages.items())), dict(sorted(cache.load_fields(document_id).items())))


def page_classes_for(document_id: str) -> dict[int, str]:
    """Page classes for breakdowns: the human-verified class where one exists (plan §9.2 P1), else the classifier's."""
    from ..dataset.page_classes import effective_classes
    from ..ingest.metadata import load_metadata
    meta = load_metadata(document_id)
    return effective_classes(document_id, {p.page: p.classification.content_kind for p in meta.pages}) if meta else {}


LAST_DETECTOR_STATS: dict[str, Any] = {}


def detect(exp: Experiment, docs: Sequence[DocumentText]) -> tuple[dict[str, list[EntitySpan]], str]:
    detector = build_detector({"detectors": exp.raw["detectors"], "combiner": exp.raw.get("combiner")})
    preds = run_detector(detector, docs)
    LAST_DETECTOR_STATS.clear()
    for d in getattr(detector, "detectors", [detector]):     # adapters: unmapped labels, dropped overlaps, restarts (counts)
        if hasattr(d, "stats"):
            LAST_DETECTOR_STATS[d.name] = d.stats()
    return preds, detector.fingerprint()


def detection_digest(exp: Experiment, docs: Sequence[DocumentText]) -> dict[str, Any]:
    preds, fp = detect(exp, docs)
    payload = det.spans_payload(preds)
    out = {"digest": det.digest(payload), "spans": len(payload), "detector": fp}
    if exp.raw.get("replacement"):
        out["replacement_digest"] = replacement_digest(exp, docs, preds)
    return out


def matters_of(docs: Sequence[DocumentText]) -> dict[str, list[DocumentText]]:
    from ..dataset.register import load_manifest
    m = {d.document_id: d.matter_id for d in load_manifest().documents}
    out: dict[str, list[DocumentText]] = {}
    for d in sorted(docs, key=lambda x: x.document_id):
        out.setdefault(m.get(d.document_id, "matter_001"), []).append(d)
    return out


def pseudonymize_all(exp: Experiment, docs: Sequence[DocumentText], preds: Mapping[str, list[EntitySpan]], vaults: dict | None = None):
    """Matter by matter, with a fresh evaluation vault (fixed test key, plan §4.7)."""
    from ..replacement.engine import pseudonymize
    from ..replacement.vault import Vault
    policy, taxonomy = load_policy(exp.policy_path()), load_taxonomy()
    out = {}
    for matter, mdocs in matters_of(docs).items():
        v = Vault.ephemeral()
        out[matter] = pseudonymize(mdocs, {d.document_id: preds.get(d.document_id, []) for d in mdocs}, policy=policy,
                                   taxonomy=taxonomy, vault=v, registry_gender=None)
        if vaults is not None:
            vaults[matter] = v
    return out


def _run_output_eval(exp: Experiment, docs, split_of, splits, golds, res, vaults) -> dict[str, Any]:
    """E9 on the replacement outputs: JSON, renders, edit log, re-identification and (optionally) PDF burn-in."""
    from ..core.types import BBox
    from ..dataset.register import document_path
    from ..evaluation import outputs as e9
    from ..replacement.pools import load_pools
    from ..reporting import leak as leakmod
    from ..taxonomy import PROTECT_ACTIONS
    ocfg = exp.raw.get("output") or {}
    pools = load_pools()
    names = set(pools.surnames) | set(pools.female) | set(pools.male) | set(pools.unisex)
    by_doc = {d.document_id: d for d in docs}
    out: dict[str, Any] = {"_config": {"formats": list(ocfg.get("formats", [])), "pdf_dpi": ocfg.get("pdf_dpi", 300),
                                       "reocr": bool(ocfg.get("reocr", False))}}
    for split in splits:
        parts = []
        for matter, r in sorted(res.items()):
            ids_ = sorted(d for d in r.documents if split_of.get(d) == split)
            if not ids_:
                continue
            sub = type(r)({k: r.documents[k] for k in ids_}, r.linking, r.dob_issued, r.summary)
            v = vaults[matter]
            srcs = {k: by_doc[k] for k in ids_}
            built = e9.build_documents(sub, srcs, v, matter)
            part = {"json": e9.outputs_metrics(built, srcs, sub, v, lambda: e9.build_documents(sub, srcs, v, matter)),
                    "roundtrip": e9.roundtrip_metrics(built, srcs, sub, v, names),
                    "downstream": e9.downstream_metrics(sub, v, names),
                    "one_way": e9.one_way_metrics(v, None)}
            if "pdf_burned" in ocfg.get("formats", []):
                regions: dict[str, dict[int, list]] = {}
                surfaces: dict[str, list] = {}
                for k in ids_:
                    g = golds.get(k)
                    if g is None:
                        continue
                    for e in g.annotation.entities:
                        if e.page and e.action in PROTECT_ACTIONS:
                            regions.setdefault(k, {}).setdefault(e.page, []).extend(BBox(x.x0, x.y0, x.x1, x.y1) for x in e.regions)
                            if e.text:
                                surfaces.setdefault(k, []).append(leakmod.Surface(e.text, e.entity_type))
                from ..output.pdf import tesseract_ocr
                part["pdf"] = e9.pdf_metrics(built, regions, source_pdf=document_path, dpi=int(ocfg.get("pdf_dpi", 300)),
                                             ocr_fn=tesseract_ocr if ocfg.get("reocr") else None, surfaces=surfaces)
            parts.append(part)
        if parts:
            out[split] = parts[0] if len(parts) == 1 else {"matters": parts}
    return out


def replacement_digest(exp: Experiment, docs: Sequence[DocumentText], preds: Mapping[str, list[EntitySpan]]) -> str:
    from ..replacement.engine import output_digest
    res = pseudonymize_all(exp, docs, preds)
    return det.digest({m: output_digest(r) for m, r in sorted(res.items())})


def _next_run_id(exp: Experiment, root: Path) -> str:
    prefix = f"{exp.experiment_id}-{exp.config_sha256[:8]}"
    n = 1 + sum(1 for p in root.glob(prefix + "-*") if p.is_dir()) if root.exists() else 1
    return f"{prefix}-{n:03d}"


def _timing(exp: Experiment, docs: Sequence[DocumentText]) -> dict[str, Any]:
    detector = build_detector({"detectors": exp.raw["detectors"], "combiner": exp.raw.get("combiner")})
    if hasattr(detector, "prepare"):
        detector.prepare(docs)
    per_page = []
    for d in docs:
        t0 = time.perf_counter()
        detector.detect(d)
        per_page.append(1000 * (time.perf_counter() - t0) / max(1, len(d.pages)))
    per_page.sort()
    if not per_page:
        return {}
    pick = (lambda q: round(per_page[min(len(per_page) - 1, int(q * (len(per_page) - 1) + 0.5))], 2))
    return {"documents": len(per_page), "ms_per_page_p50": pick(0.5), "ms_per_page_p95": pick(0.95)}


def run(path: str | Path, *, splits: Sequence[str] | None = None, allow_test: bool = False,
        in_process: int | None = None, fresh: int | None = None, runs_root: Path | None = None,
        golds: Mapping[str, GoldDocument] | None = None) -> dict[str, Any]:
    exp = load_experiment(path)
    splits = list(splits or exp.splits())
    if "test" in splits and not allow_test:
        raise ExperimentError("the test split is scored only with --allow-test (never for rule tuning)")
    n_in, n_fresh = exp.determinism()
    n_in = n_in if in_process is None else in_process
    n_fresh = n_fresh if fresh is None else fresh
    docs_meta = dataset_documents(splits)
    if not docs_meta:
        raise ExperimentError("no documents in the selected splits")
    docs = [load_document_text(d) for d, _ in docs_meta]
    split_of = dict(docs_meta)
    policy = load_policy(exp.policy_path())
    taxonomy = load_taxonomy()

    # Detection, repeated in-process for the determinism harness; the first run is the result.
    runs = det.in_process(lambda: det.spans_payload(detect(exp, docs)[0]), max(1, n_in))
    preds, fingerprint = detect(exp, docs)
    det_stats = dict(LAST_DETECTOR_STATS)
    ref_payload = det.spans_payload(preds)
    reference = det.digest(ref_payload)
    fresh_runs = det.fresh_process(["experiment", "digest", str(exp.path), "--split", *splits]
                                   + (["--allow-test"] if allow_test else []), det.DEFAULT_FRESH_SEEDS[:n_fresh]) if n_fresh else []
    determinism = det.summarize(reference, runs, fresh_runs, ref_payload)
    runtime = _timing(exp, docs)

    golds = dict(golds) if golds is not None else {d.document_id: g for d in docs if (g := load_gold(d.document_id)) is not None}
    page_classes = {d.document_id: page_classes_for(d.document_id) for d in docs}
    split_public: dict[str, Any] = {}
    split_private: dict[str, Any] = {}
    for split in splits:
        sdocs = [d for d in docs if split_of[d.document_id] == split]
        if not sdocs:
            continue
        from ..dataset.campaign import load_transcripts
        transcripts = {d.document_id: t for d in sdocs if (t := load_transcripts(d.document_id))}   # verified only
        pub, priv, mentions = evaluate_split(sdocs, golds, preds, page_classes=page_classes, policy=policy, taxonomy=taxonomy,
                                             split=split, transcripts=transcripts or None)
        orc = _oracle_eval(exp, sdocs, golds, preds, page_classes, policy, taxonomy, split, priv)
        if orc is not None:
            pub["ocr_vs_oracle"] = orc
            from ..evaluation import attribution as _attr
            pub["attribution"] = _attr.summarize(priv["attribution"])
        split_public[split] = pub
        split_private[split] = {**priv, "_mentions": mentions}

    replacement_public: dict[str, Any] = {}
    replacement_det: dict[str, Any] = {}
    outputs_private: dict[str, Any] = {}
    if exp.raw.get("replacement"):
        from ..evaluation.privacy import evaluate_outputs
        from ..replacement.engine import output_digest
        from ..replacement.pools import load_pools
        vaults: dict = {}
        res = pseudonymize_all(exp, docs, preds, vaults)
        ref_rd = det.digest({m: output_digest(r) for m, r in sorted(res.items())})
        rd_runs = [replacement_digest(exp, docs, preds) for _ in range(max(0, n_in - 1))]
        fresh_rd = [r.get("replacement_digest") for r in fresh_runs]
        ip = {"runs": 1 + len(rd_runs), "identical": 1 + sum(1 for x in rd_runs if x == ref_rd)}
        fp_ = {"runs": len(fresh_rd), "identical": sum(1 for x in fresh_rd if x == ref_rd)}
        replacement_det = {"reference_digest": ref_rd[:16], "in_process": ip, "fresh_process": fp_,
                           "total_runs": ip["runs"] + fp_["runs"], "identical": ip["identical"] + fp_["identical"]}
        replacement_det["all_identical"] = replacement_det["identical"] == replacement_det["total_runs"]
        reg_gender = _registry_gender()
        by_doc = {d.document_id: d for d in docs}
        for split in splits:
            sdoc_ids = {d.document_id for d in docs if split_of[d.document_id] == split}
            if not sdoc_ids or split not in split_private:
                continue
            gold_by_doc: dict[str, list] = {}
            for m in split_private[split]["_mentions"]:
                gold_by_doc.setdefault(m.document_id, []).append(m)
            parts = []
            for matter, r in sorted(res.items()):
                ids_ = sorted(set(r.documents) & sdoc_ids)
                if not ids_:
                    continue
                sub = type(r)({k: r.documents[k] for k in ids_}, r.linking, r.dob_issued, r.summary)
                pools = load_pools([pe.text for k in ids_ for pe in by_doc[k].pages.values()])
                parts.append(evaluate_outputs({k: gold_by_doc.get(k, []) for k in ids_}, sub, {k: by_doc[k] for k in ids_},
                                              taxonomy=taxonomy, pools=pools, registry_gender=reg_gender))
            replacement_public[split] = parts[0] if len(parts) == 1 else {"matters": parts}
        for matter, r in sorted(res.items()):
            replacement_public.setdefault("_summary", {})[matter] = r.summary
            for doc_id, dr in r.documents.items():
                outputs_private[doc_id] = {"pages": dr.pages, "fields": dr.fields, "edits": [e.to_dict() for e in dr.edits],
                                           "reviews": dr.reviews}
    outputs_public: dict[str, Any] = {}
    if exp.raw.get("output") and exp.raw.get("replacement"):
        outputs_public = _run_output_eval(exp, docs, split_of, splits, golds, res, vaults)
    gate_public: dict[str, Any] = {}
    if exp.raw.get("gate"):
        gate_public = _run_gate_eval(exp, docs, split_of, splits, golds, policy, taxonomy)
        _apply_gate_flags(gate_public.pop("_flagged", {}), split_public, split_private)
    for v_ in split_private.values():
        v_.pop("_mentions", None)

    root = runs_root or runs_dir()
    run_id = _next_run_id(exp, root)
    run_dir = root / run_id
    manifest_path = golden_dir() / "manifest.json"
    manifest = run_manifest(run_id=run_id, experiment=exp.raw, config_sha256=exp.config_sha256,
                            dataset_manifest_sha256=sha256_file(manifest_path) if manifest_path.exists() else None,
                            detector_fingerprint=fingerprint,
                            text_sources={d.document_id: [pe.text_source_id for pe in d.pages.values()] for d in docs})
    results = {"schema": "redactor.results", "schema_version": "0.1.0", "run_id": run_id, "experiment_id": exp.experiment_id,
               "config_sha256": exp.config_sha256, "detector": fingerprint, "policy": policy.ref, "taxonomy": taxonomy.version,
               "splits": split_public, "determinism": determinism, "runtime": runtime,
               "replacement": replacement_public or None, "replacement_determinism": replacement_det or None,
               "gate": gate_public or None, "outputs": outputs_public or None,
               "detector_stats": dict(sorted(det_stats.items())) or None,
               "page_classification": _e0(docs),
               "dataset_quality": _dataset_quality(),
               "gold_status": {s: v["gold_status"] for s, v in split_public.items()}}
    write_public(run_dir / "public", results, manifest)

    # Leak test over every public artifact, against every available gold surface.
    surfaces = []
    for doc_id in sorted({d for d, _ in dataset_documents(["dev", "test"])} | set(golds)):
        g = golds.get(doc_id) or load_gold(doc_id)
        if g is None:
            continue
        for e in g.annotation.entities:
            if e.text and (e.action in ("SYNTHETIC", "REDACT") or policy.is_critical(e.entity_type, e.role)):
                surfaces.append(leak.Surface(e.text, e.entity_type))
    needles = leak.build_needles(surfaces)
    summary, detail = leak.scan_dir(run_dir / "public", needles)
    results["leak_test"] = summary
    write_public(run_dir / "public", results, manifest)
    summary2, detail2 = leak.scan_dir(run_dir / "public", needles)   # rescan the final files
    results["leak_test"] = summary2
    (run_dir / "public" / "report.md").write_text(report_md(results), encoding="utf-8", newline="\n")
    final, final_detail = leak.scan_dir(run_dir / "public", needles)
    write_private(run_dir / "private", preds=preds, splits=split_private, leak_detail=detail + detail2 + final_detail)
    if outputs_private:
        from ..core.canonical import write_canonical
        for doc_id, o in sorted(outputs_private.items()):
            write_canonical(run_dir / "private" / "outputs" / f"{doc_id}.json", o)
    ok = final["passed"] and determinism["all_identical"] and (not replacement_det or replacement_det["all_identical"])
    return {"ok": ok, "run_id": run_id, "experiment_id": exp.experiment_id, "documents": len(docs),
            "splits": {s: {"gold_status": v["gold_status"], "overlap_any_f1": v["detection"]["overlap_any"]["micro"]["f1"],
                           "strict_f1": v["detection"]["strict"]["micro"]["f1"],
                           "protection_recall": v["protection"]["pessimistic"]["mention_recall"]} for s, v in split_public.items()},
            "determinism": f"{determinism['identical']}/{determinism['total_runs']}",
            "replacement_determinism": f"{replacement_det['identical']}/{replacement_det['total_runs']}" if replacement_det else None,
            "gate": {s_: {k: g.get(k) for k in ("gate_recall", "unsafe_passes", "review_load", "retry_rescue_rate", "leak_scan_hits")}
                     for s_, g in gate_public.items() if not s_.startswith("_")} or None,
            "residual_after_replacement": {k: (v2.get("residual") or {}).get("pessimistic") for k, v2 in replacement_public.items()
                                           if not k.startswith("_") and isinstance(v2, dict)} or None,
            "leak_test": {"hits": final["hits"], "passed": final["passed"], "files": final["files_scanned"]},
            "public_dir": str((run_dir / "public").relative_to(root.parent)) if root.parent in (run_dir / "public").parents else str(run_dir / "public")}


def _oracle_eval(exp, sdocs, golds, preds, page_classes, policy, taxonomy, split, priv) -> dict[str, Any] | None:
    """Detection on the oracle text source (verified gold transcripts, plan §4.3) next to detection on the
    OCR text of the same pages; and attribution refined with it (§4.6): a mention the detector also misses
    on perfect text is a DETECTOR_MISS, not an OCR_INDUCED_MISS. None while no transcript is verified."""
    from ..evaluation.oracle import oracle_documents, restrict, restrict_gold
    odocs = {k: v for k, v in oracle_documents(sdocs).items() if k in golds}
    if not odocs:
        return None
    pages = {k: sorted(v.pages) for k, v in odocs.items()}
    o_list = [odocs[k] for k in sorted(odocs)]
    c_list = [restrict(d, pages[d.document_id]) for d in sdocs if d.document_id in odocs]
    g_sub = {k: restrict_gold(golds[k], pages[k]) for k in odocs}
    o_preds, _ = detect(exp, o_list)
    c_preds = {d.document_id: [s for s in preds.get(d.document_id, []) if s.page in set(pages[d.document_id])] for d in c_list}
    kw = dict(page_classes=page_classes, policy=policy, taxonomy=taxonomy, split=split, bootstrap_rounds=0)
    pub_o, priv_o, _ = evaluate_split(o_list, g_sub, o_preds, **kw)
    pub_c, _, _ = evaluate_split(c_list, g_sub, c_preds, **kw)
    oracle_protected = {m["id"]: bool(m.get("protected")) for m in priv_o["mentions"]}
    detected = {m["id"]: bool(m.get("detected")) for m in priv["mentions"]}
    changed = 0
    for r in priv["attribution"]:
        if r["outcome"] == "OCR_INDUCED_MISS" and not detected.get(r["id"]) and oracle_protected.get(r["id"]) is False:
            r["outcome"] = "DETECTOR_MISS"
            changed += 1
    pick = (lambda p_: {"overlap_any": p_["detection"]["overlap_any"]["micro"], "strict_f1": p_["detection"]["strict"]["micro"]["f1"],
                        "protection_recall": p_["protection"]["pessimistic"]["mention_recall"]})
    return {"documents": sorted(odocs), "pages": sum(len(v) for v in pages.values()), "ocr": pick(pub_c), "oracle": pick(pub_o),
            "attribution_reclassified_to_detector_miss": changed}


def _dataset_quality() -> dict[str, Any] | None:
    """The dataset-quality table (plan §4.8, §9.3): every agreement file written by `redactor iaa`
    (A1 vs A2, silver vs adjudicated gold, A1 test-retest), metrics only."""
    d = golden_dir() / "iaa"
    out = {}
    for p in sorted(d.glob("iaa*.public.json")) if d.exists() else []:
        data = read_json(p)
        rows = {}
        for doc, v in sorted(data.get("documents", {}).items()):
            if "span" not in v:
                continue
            rows[doc] = {"pages": v.get("pages_compared"), "strict_f1": v["span"].get("strict", {}).get("a_as_gold", {}).get("f1"),
                         "overlap_any_f1": v["span"].get("overlap_any", {}).get("a_as_gold", {}).get("f1"),
                         "kappa_protect": (v.get("kappa") or {}).get("protect_binary"),
                         "b3_f1": (v.get("canonical_b3") or {}).get("f1")}
        out[f"{data.get('a')} vs {data.get('b')}"] = {"kind": data.get("kind", "inter_annotator"), "documents": rows}
    return out or None


def _e0(docs) -> dict[str, Any]:
    from ..dataset.page_classes import e0_metrics
    return e0_metrics([d.document_id for d in docs])


def _apply_gate_flags(flagged: Mapping[str, Any], split_public: dict, split_private: dict) -> None:
    """Attribution `gate_flagged` (plan §4.6): a failed gold-protect mention is flagged when the release gate
    routed its page to REVIEW or BLOCKED (a document-level field: when its document was not exportable)."""
    from ..evaluation import attribution as attr
    pages = {tuple(x) for x in flagged.get("pages", [])}
    docs_blocked = set(flagged.get("documents", []))
    for split, priv in split_private.items():
        page_of = {m["id"]: m.get("page") for m in priv.get("mentions", [])}
        rows = priv.get("attribution", [])
        for r in rows:
            pg = page_of.get(r["id"])
            r["gate_flagged"] = (r["document_id"], pg) in pages if pg is not None else r["document_id"] in docs_blocked
        if split in split_public and rows:
            split_public[split]["attribution"] = attr.summarize(rows)
            split_public[split]["attribution"]["gate_run"] = True


def _run_gate_eval(exp: Experiment, docs, split_of, splits, golds, policy, taxonomy) -> dict[str, Any]:
    """Release gate per matter on the experiment's documents, scored with E8 and E5 on the chosen text."""
    from ..evaluation.gate_metrics import gate_metrics
    from ..evaluation.gold import project_document
    from ..evaluation.privacy import evaluate_outputs
    from ..gate.gate import run_gate
    from ..ingest.metadata import load_metadata
    from ..replacement.pools import load_pools
    from ..replacement.vault import Vault
    gate_cfg = yaml.safe_load((REPO_ROOT / exp.raw["gate"]).read_text(encoding="utf-8"))
    det_cfg = {"detectors": exp.raw["detectors"], "combiner": exp.raw.get("combiner")}
    out: dict[str, Any] = {"_config": {"version": gate_cfg.get("version"), "provisional": gate_cfg.get("provisional"),
                                       "thresholds": gate_cfg.get("thresholds"), "second_engine": gate_cfg.get("second_engine")}}
    reg_gender = _registry_gender()
    for matter, mdocs in matters_of(docs).items():
        info = {}
        hand: dict[str, set[int]] = {}
        for d in mdocs:
            meta = load_metadata(d.document_id)
            info[d.document_id] = {p.page: {"class": p.classification.content_kind,
                                            "method": p.extraction.method if p.extraction else None} for p in meta.pages}
            g = golds.get(d.document_id)
            hand[d.document_id] = {e.page for e in g.annotation.entities if e.page and "handwritten" in e.flags} if g else set()
        res = run_gate({d.document_id: d for d in mdocs}, info, detector_cfg=det_cfg, policy=policy, taxonomy=taxonomy,
                       gate_cfg=gate_cfg, vault=Vault.ephemeral(), handwriting=hand)
        out.setdefault("_summary", {})[matter] = res.summary()
        # private to this function's caller (popped before publishing): what the gate routed to a person
        fl = out.setdefault("_flagged", {"pages": [], "documents": []})
        fl["pages"] += [(p.document_id, p.page) for p in res.pages if p.state in ("REVIEW", "BLOCKED")]
        fl["documents"] += [d for d, ok in res.export_allowed.items() if not ok]
        # gate determinism: repeat the whole gate (retries, leak scan, outputs) and compare digests
        digests = [res.digest()] + [run_gate({d.document_id: d for d in mdocs}, info, detector_cfg=det_cfg, policy=policy,
                                             taxonomy=taxonomy, gate_cfg=gate_cfg, vault=Vault.ephemeral(), handwriting=hand).digest()
                                    for _ in range(max(0, int(exp.raw.get("gate_runs", 2)) - 1))]
        out.setdefault("_determinism", {})[matter] = {"runs": len(digests), "identical": sum(1 for x in digests if x == digests[0]),
                                                      "digest": digests[0][:16]}
        for split in splits:
            ids_ = sorted(d.document_id for d in mdocs if split_of[d.document_id] == split)
            if not ids_:
                continue
            gold_on_chosen = {k: project_document(golds[k], res.chosen[k], policy=policy, taxonomy=taxonomy,
                                                  page_classes={p: v["class"] for p, v in info[k].items()}) for k in ids_ if k in golds}
            sub_pages = [p for p in res.pages if p.document_id in ids_]
            sub = type(res)(sub_pages, [i for i in res.items if i["document_id"] in ids_], [h for h in res.hits if h.document_id in ids_],
                            type(res.matter)({k: res.matter.documents[k] for k in ids_}, res.matter.linking, res.matter.dob_issued, res.matter.summary),
                            {k: res.chosen[k] for k in ids_}, {k: res.spans[k] for k in ids_}, res.regions,
                            {k: res.export_allowed[k] for k in ids_},
                            [c for c in res.engine_comparison if c["document_id"] in ids_])
            g8 = gate_metrics(sub, gold_on_chosen)
            pools = load_pools([pe.text for k in ids_ for pe in res.chosen[k].pages.values()])
            g8["e5_after_gate"] = evaluate_outputs(gold_on_chosen, sub.matter, {k: res.chosen[k] for k in ids_}, taxonomy=taxonomy,
                                                   pools=pools, registry_gender=reg_gender)
            out[split] = g8
    return out


def _registry_gender() -> dict[str, str]:
    """canonical_id -> gender from the gold registry, else the silver registry."""
    from ..dataset.models import CanonicalRegistry
    for p in (golden_dir() / "registry" / "matter_001.entities.json",
              golden_dir() / "annotations_raw" / "claude_silver" / "registry" / "matter_001.entities.json"):
        if p.exists():
            reg = CanonicalRegistry.model_validate(read_json(p))
            return {e.canonical_id: e.gender for e in reg.entities if e.gender}
    return {}


def digest_main(path: str, splits: Sequence[str], allow_test: bool) -> dict[str, Any]:
    exp = load_experiment(path)
    if "test" in splits and not allow_test:
        raise ExperimentError("test split needs --allow-test")
    docs = [load_document_text(d) for d, _ in dataset_documents(splits)]
    return detection_digest(exp, docs)
