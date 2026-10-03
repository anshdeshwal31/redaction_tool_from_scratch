"""Public run outputs (plan §4.8): results.json, results.csv and report.md. Metrics, counts and IDs only.
There is no single overall score. Scores against unverified gold are labelled silver."""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any, Mapping

from ..core.canonical import write_canonical


def _f(x: Any, pct: bool = False) -> str:
    if x is None:
        return "n/a"
    if isinstance(x, float):
        return f"{100 * x:.1f}%" if pct else f"{x:.4f}"
    return str(x)


def _ms(x: Any) -> str:
    return "n/a" if x is None else f"{x:.1f}"


def csv_rows(results: Mapping[str, Any]) -> list[list[Any]]:
    rows = [["split", "scheme", "scope", "key", "tp", "fp", "fn", "precision", "recall", "f1", "gold_status"]]
    for split, sr in sorted(results.get("splits", {}).items()):
        det = sr.get("detection", {})
        for scheme in ("strict", "exact_boundary", "overlap_typed", "overlap_any"):
            d = det.get(scheme)
            if not d:
                continue
            m = d["micro"]
            rows.append([split, scheme, "micro", "all", m["tp"], m["fp"], m["fn"], m["precision"], m["recall"], m["f1"], sr["gold_status"]])
            for scope in ("by_type", "by_group", "by_bucket", "by_page_class", "by_document"):
                for key, v in d.get(scope, {}).items():
                    rows.append([split, scheme, scope, key, v["tp"], v["fp"], v["fn"], v["precision"], v["recall"], v["f1"], sr["gold_status"]])
    return rows


def write_csv(results: Mapping[str, Any], path: Path) -> None:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    for r in csv_rows(results):
        w.writerow(["" if x is None else x for x in r])
    path.write_text(buf.getvalue(), encoding="utf-8", newline="\n")


def _bucket_table(d: Mapping[str, Any], scope: str, title: str) -> list[str]:
    out = [f"| {title} | TP | FP | FN | P | R | F1 |", "|---|---:|---:|---:|---:|---:|---:|"]
    for k, v in d.get(scope, {}).items():
        out.append(f"| {k} | {v['tp']} | {v['fp']} | {v['fn']} | {_f(v['precision'])} | {_f(v['recall'])} | {_f(v['f1'])} |")
    return out


def report_md(results: Mapping[str, Any]) -> str:
    L: list[str] = []
    run = results["run_id"]
    L.append(f"# Run `{run}`")
    L.append("")
    L.append(f"Experiment `{results['experiment_id']}` · detector `{results['detector']}` · config `{results['config_sha256'][:12]}`")
    L.append("")
    statuses = {s: v["gold_status"] for s, v in results["splits"].items()}
    if any(v != "verified" for v in statuses.values()):
        L.append("**Gold status: " + ", ".join(f"{s} = {v.upper()}" for s, v in sorted(statuses.items())) +
                 ".** Scores against unverified (silver) gold are not trusted results (plan §9.4).")
        L.append("")
    det = results.get("determinism", {})
    rt = results.get("runtime", {})
    repl = results.get("replacement") or {}
    rdet = results.get("replacement_determinism") or {}
    L.append("## Comparison table")
    L.append("")
    L.append("| Experiment | Split | Documents | PII P | PII R | F1 | Residual PII | Protected-info preservation | Entity consistency | Detection determinism | Replacement determinism | OCR | Runtime |")
    L.append("|---|---|---:|---:|---:|---:|---:|---:|---|---|---|---:|---:|")
    for split, sr in sorted(results["splits"].items()):
        m = sr["detection"]["overlap_any"]["micro"]
        prot = sr["protection"]["pessimistic"]
        keep = sr["protection"]["pessimistic"].get("keep_mentions") or 0
        kv = prot["keep_violations"]
        pres = (1 - kv / keep) if keep else None
        ocr = sr["ocr"]["entity_surface_recovery"]["all"]
        tot = sum(ocr.values())
        ocr_s = _f(ocr["exact"] / tot, pct=True) + " exact" if tot else "n/a"
        detd = f"{det.get('identical', 0)}/{det.get('total_runs', 0)}" if det else "n/a"
        rs = repl.get(split) if isinstance(repl.get(split), dict) else None
        if rs and "residual" in rs:
            res_s = f"{rs['residual']['pessimistic']}/{rs['residual']['gold_protect']} (after replacement)"
            cons_s = _f(rs["consistency"]["alias_consistency"], pct=True) + " alias"
            pres = rs["preservation"]["keep_rate"]
        else:
            res_s = f"{prot['residual_mentions']}/{prot['gold_protect']}"
            cons_s = "n/a (R1)"
        repd = f"{rdet.get('identical', 0)}/{rdet.get('total_runs', 0)}" if rdet else "n/a (R1)"
        L.append(f"| {results['experiment_id']} ({sr['gold_status']}) | {split} | {len(sr['documents'])} | {_f(m['precision'])} | {_f(m['recall'])} | "
                 f"{_f(m['f1'])} | {res_s} | {_f(pres, pct=True)} | {cons_s} | {detd} | {repd} | {ocr_s} | "
                 f"{_ms(rt.get('ms_per_page_p50'))} ms/page |")
    L.append("")
    L.append("PII P/R/F1: overlap-any matching, micro. Residual PII: gold mentions to protect that the predicted protect spans do not fully cover (pessimistic: REVIEW counts as leaked).")
    L.append("")
    for split, sr in sorted(results["splits"].items()):
        d = sr["detection"]
        L.append(f"## Split `{split}` ({sr['gold_status']}): {len(sr['documents'])} documents, {sr['pages']} pages")
        L.append("")
        L.append("| Scheme | TP | FP | FN | P | R | F1 | Macro F1 |")
        L.append("|---|---:|---:|---:|---:|---:|---:|---:|")
        for scheme in ("strict", "exact_boundary", "overlap_typed", "overlap_any"):
            m = d[scheme]["micro"]
            L.append(f"| {scheme} | {m['tp']} | {m['fp']} | {m['fn']} | {_f(m['precision'])} | {_f(m['recall'])} | {_f(m['f1'])} | {_f(d[scheme]['macro_f1'])} |")
        L.append("")
        bs = sr.get("bootstrap", {})
        if bs:
            L.append("Document-level bootstrap 95% intervals: " + "; ".join(
                f"{k} [{_f(v.get('lo'))}, {_f(v.get('hi'))}] (n={v.get('n_docs')})" for k, v in sorted(bs.items())))
            L.append("")
        L.append("### By report bucket (overlap-any)")
        L += _bucket_table(d["overlap_any"], "by_bucket", "Bucket")
        L.append("")
        L.append("### By page class (overlap-any)")
        L += _bucket_table(d["overlap_any"], "by_page_class", "Page class")
        L.append("")
        L.append("### By document (overlap-any)")
        L += _bucket_table(d["overlap_any"], "by_document", "Document")
        L.append("")
        L.append("### By type (strict)")
        L += _bucket_table(d["strict"], "by_type", "Type")
        L.append("")
        L.append("### Protection")
        L.append("")
        L.append("| Mode | Gold protect | Mention recall | Char recall | Pred protect | Mention precision | Char precision | KEEP violations | Critical missed |")
        L.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        for mode, p in sorted(sr["protection"].items()):
            L.append(f"| {mode} | {p['gold_protect']} | {_f(p['mention_recall'])} | {_f(p['char_recall'])} | {p['pred_protect']} | "
                     f"{_f(p['mention_precision'])} | {_f(p['char_precision'])} | {p['keep_violations']} | {p['critical']['missed']}/{p['critical']['total']} |")
        L.append("")
        a = sr["attribution"]
        L.append("### Attribution of gold-protect mentions (plan §4.6)")
        L.append("")
        outs = ("OK", "DETECTOR_MISS", "OCR_INDUCED_MISS", "OCR_LOSS", "POLICY_MISS", "REPLACEMENT_MISS")
        L.append("| Page class | " + " | ".join(outs) + " |")
        L.append("|---|" + "---:|" * len(outs))
        for pc, c in a["by_page_class"].items():
            L.append(f"| {pc} | " + " | ".join(str(c[o]) for o in outs) + " |")
        L.append(f"| **all** | " + " | ".join(str(a["all"][o]) for o in outs) + " |")
        L.append("")
        gate_note = "routed to REVIEW/BLOCKED by the release gate" if a.get("gate_run") else "release gate not part of this run"
        L.append(f"Gate-flagged failures: {a['gate_flagged_failures']} ({gate_note}). Critical failures: {a['critical_failures']}.")
        L.append("")
        o = sr["ocr"]
        L.append("### OCR")
        L.append("")
        L.append("Mean word confidence by page class: " + ", ".join(f"{k} {_f(v)}" for k, v in o["mean_word_confidence"].items()) + ".")
        rec = o["entity_surface_recovery"]["all"]
        L.append(f"Entity-surface recovery (scored gold mentions): exact {rec['exact']}, near {rec['near']}, degraded {rec['degraded']}, missing {rec['missing']}.")
        L.append(f"Transcript metrics: {o['transcripts'].get('pages', 0)} pages.")
        ov = sr.get("ocr_vs_oracle")
        if ov:
            L.append(f"Detection on OCR text vs the oracle (verified transcripts, {ov['pages']} pages): overlap-any recall "
                     f"{_f(ov['ocr']['overlap_any']['recall'])} vs {_f(ov['oracle']['overlap_any']['recall'])}, protection recall "
                     f"{_f(ov['ocr']['protection_recall'])} vs {_f(ov['oracle']['protection_recall'])}; "
                     f"{ov['attribution_reclassified_to_detector_miss']} misses re-attributed from OCR to the detector.")
        else:
            L.append("Oracle text source: no verified transcript yet (A2).")
        L.append("")
    for split, rs in sorted(repl.items()):
        if split.startswith("_") or not isinstance(rs, dict) or "residual" not in rs:
            continue
        L.append(f"## Replacement and privacy (E5), split `{split}`")
        L.append("")
        r_ = rs["residual"]
        L.append(f"- Residual PII after replacement: {r_['pessimistic']}/{r_['gold_protect']} pessimistic (REVIEW counts as leaked), "
                 f"{r_['with_review']}/{r_['gold_protect']} with review; critical residual {r_['critical']['residual']}/{r_['critical']['total']}.")
        L.append(f"- Leak scan of the pseudonymised text against gold surfaces: {rs['leak_scan']['hits']} hits.")
        pv = rs["preservation"]
        L.append(f"- KEEP preservation {_f(pv['keep_rate'], pct=True)} ({pv['keep_preserved']}/{pv['keep_mentions']}); non-PII character preservation "
                 f"{_f(pv['non_pii_char_preservation'], pct=True)}; over-redacted characters {pv['over_redacted_chars']}.")
        db = rs["dob"]
        L.append(f"- DOB: {db['issued']} age-preserving surrogates, {db['token_fallback']} token fallbacks; age preserved {_f(db['age_preserved'], pct=True)}, "
                 f"changed {_f(db['changed'], pct=True)}, birth year kept {_f(db['year_kept'], pct=True)}.")
        rc = rs["redact_class"]
        L.append("- REDACT-class: " + "; ".join(f"{t} recall {_f(v['recall'], pct=True)} ({v['redacted']}/{v['gold']}), false redactions {v['false_redactions']}"
                                                 for t, v in sorted(rc.items())) + ".")
        ids = rs["identifier_safety"]
        L.append(f"- Identifier safety: checksum-invalid {_f(ids['checksum_invalid_share'], pct=True)} of {ids['checksummed_surrogates']}; phones in reserved ranges "
                 f"{_f(ids['phones_reserved_share'], pct=True)} of {ids['phones']}; emails on reserved domains {_f(ids['emails_reserved_share'], pct=True)} of {ids['emails']}; "
                 f"surrogates found in the source text {ids['surrogates_in_source_text']}.")
        c = rs["consistency"]
        L.append(f"- Consistency: alias {_f(c['alias_consistency'], pct=True)} over {c['with_2plus_mentions']} persons with 2+ mentions; collisions {c['collisions']}; "
                 f"cross-document {_f(c['cross_document_consistency'], pct=True)} ({c['cross_document_entities']}); grouping pairwise F1 {_f(c['grouping_pairwise']['f1'])}, "
                 f"B³ F1 {_f(c['grouping_b3']['f1'])}; gender preservation {_f(c['gender_preservation'], pct=True)} of {c['gender_checked']}; "
                 f"'Mr' + female surrogate {c['male_honorific_with_female_name']}.")
        L.append("")
    if rdet:
        L.append(f"Replacement determinism (fixed test key): {rdet['identical']}/{rdet['total_runs']} identical output digests "
                 f"({rdet['in_process']['identical']}/{rdet['in_process']['runs']} in-process, {rdet['fresh_process']['identical']}/{rdet['fresh_process']['runs']} fresh).")
        L.append("")
    if det:
        L.append("## Determinism (plan §4.7)")
        L.append("")
        L.append(f"Detection on frozen extraction: {det['identical']}/{det['total_runs']} runs identical "
                 f"({det['in_process']['identical']}/{det['in_process']['runs']} in-process, "
                 f"{det['fresh_process']['identical']}/{det['fresh_process']['runs']} fresh processes, PYTHONHASHSEED {det['fresh_process']['seeds']}). "
                 f"Reference digest `{det['reference_digest']}`.")
        L.append("")
    if rt:
        L.append(f"Runtime (separate timing run, detection only): p50 {_ms(rt.get('ms_per_page_p50'))} ms/page, p95 {_ms(rt.get('ms_per_page_p95'))} ms/page over {rt.get('documents')} documents.")
        L.append("")
    dq = results.get("dataset_quality") or {}
    L.append("## Dataset quality (plan §9.3)")
    L.append("")
    if dq:
        L.append("| Pair | Kind | Document | Pages | Strict F1 | Overlap-any F1 | κ protect | B³ F1 |")
        L.append("|---|---|---|---:|---:|---:|---:|---:|")
        for pair, v in sorted(dq.items()):
            for doc, r in v["documents"].items():
                L.append(f"| {pair} | {v['kind']} | {doc} | {r['pages']} | {_f(r['strict_f1'])} | {_f(r['overlap_any_f1'])} | "
                         f"{_f(r['kappa_protect'])} | {_f(r['b3_f1'])} |")
        L.append("")
        L.append("Detector scores within this agreement noise cannot be ranked against each other.")
    else:
        L.append("No agreement figures yet: the A1/A2 campaign (A2) produces them (`redactor iaa`).")
    L.append("")
    e0 = results.get("page_classification") or {}
    if e0:
        L.append("## Page classification (E0)")
        L.append("")
        if e0.get("verified_pages"):
            L.append(f"Classifier vs human-verified classes: accuracy {_f(e0['accuracy'])} ({e0['correct']}/{e0['verified_pages']} "
                     f"verified pages of {e0['pages']}; {e0['status']}). Confusion (classifier -> verified): "
                     + "; ".join(f"{k} -> " + ", ".join(f"{g} {n}" for g, n in v.items()) for k, v in e0["confusion_classifier_to_verified"].items()) + ".")
        else:
            L.append(f"No page class verified yet (0 of {e0.get('pages', 0)} pages): plan §9.2 P1 is human work.")
        L.append("")
    gate = results.get("gate") or {}
    for split, g in sorted(gate.items()):
        if split.startswith("_") or not isinstance(g, dict) or "gate_recall" not in g:
            continue
        cfg = gate.get("_config", {})
        L.append(f"## Release gate (E8), split `{split}`" + (" (PROVISIONAL thresholds)" if cfg.get("provisional") else ""))
        L.append("")
        L.append("| Gate recall | Unsafe passes | Pages | Blocked | Review load | Retry rescue | Leak-scan hits | Leak-scan precision (vs silver) |")
        L.append("|---:|---:|---:|---:|---:|---:|---:|---:|")
        L.append(f"| {_f(g['gate_recall'])} | {g['unsafe_passes']} | {g['pages']} | {g['blocked_pages']} | {_f(g['review_load'])} | "
                 f"{_f(g['retry_rescue_rate'])} | {g['leak_scan_hits']} | {_f(g['leak_scan_precision'])} |")
        L.append("")
        L.append("Review by reason: " + (", ".join(f"{k} {v}" for k, v in g["review_by_reason"].items()) or "none") +
                 ". Rescued by rung: " + (", ".join(f"{k} {v}" for k, v in g["rescued_by_rung"].items()) or "none") + ".")
        ec = g.get("engine_comparison", {})
        if ec.get("pages"):
            L.append(f"Engine comparison on {ec['pages']} pages: bag-of-words F1 by class " +
                     ", ".join(f"{k} {_f(v)}" for k, v in ec["mean_bow_f1_by_class"].items()) +
                     f"; mean confidence Tesseract {_f(ec['mean_conf']['tesseract'])}, RapidOCR {_f(ec['mean_conf']['rapidocr'])}.")
        gd = gate.get("_determinism", {})
        if gd:
            L.append("Gate determinism: " + "; ".join(f"{m} {v['identical']}/{v['runs']} identical" for m, v in sorted(gd.items())) + ".")
        L.append("")
    outs = results.get("outputs") or {}
    for split, o in sorted(outs.items()):
        if split.startswith("_") or not isinstance(o, dict):
            continue
        L.append(f"## Outputs and re-identification (E9), split `{split}`")
        L.append("")
        j = o["json"]
        L.append(f"- JSON: {j['schema_valid']}/{j['documents']} schema-valid, {j['render_deterministic']}/{j['documents']} render-deterministic "
                 f"(JSON, text, Markdown), edit log complete {j['edit_log_complete']}/{j['documents']} ({j['edits']} edits); originals found "
                 f"outside the page texts: {j['original_text_hits_outside_page_text']}.")
        rt_ = o["roundtrip"]
        L.append(f"- Round trip: {rt_['restored_exactly']}/{rt_['synthetic_edits']} SYNTHETIC edits restored exactly ({_f(rt_['roundtrip_rate'], pct=True)}); "
                 f"pages restored exactly {rt_['pages_restored_exactly']}/{rt_['pages']}; REDACT tokens restored {rt_['redact_tokens_restored']}.")
        ds = o["downstream"]
        L.append(f"- Simulated downstream answers: {ds['n']} sentences over {ds['identities']} identities; restoration "
                 f"{_f(ds['restoration_rate'], pct=True)}, partial (surrogates left, safe) {_f(ds['partial_rate'], pct=True)}, ambiguity {_f(ds['ambiguity_rate'], pct=True)}, wrong restoration "
                 f"{_f(ds['wrong_restoration_rate'], pct=True)} (target 0).")
        ow = o["one_way"]
        L.append(f"- One-way: REDACT-class types with plaintext in the vault {ow['redact_types_with_plaintext_in_vault']} (target 0).")
        pd_ = o.get("pdf")
        if pd_:
            reocr = pd_["reocr_gold_hits"] if pd_["reocr_gold_hits"] is not None else "not run"
            L.append(f"- PDF burn-in: container checks {pd_['container_checks_passed']}/{pd_['documents']}, writer byte-deterministic "
                     f"{pd_['writer_byte_deterministic']}/{pd_['documents']}; gold-protect region area covered {_f(pd_['gold_area_covered'], pct=True)}, "
                     f"collateral share {_f(pd_['collateral_share'], pct=True)}; re-OCR gold hits {reocr}"
                     + (f" over {pd_['reocr_pages']} pages" if pd_["reocr_pages"] else "") + ".")
        L.append("")
    L.append("## Not part of this run")
    L.append("")
    if not repl:
        L.append("- Entity-consistency and replacement metrics (R1) need `replacement:` in the experiment.")
    if not gate:
        L.append("- Release-gate table (G1) needs `gate:` in the experiment.")
    if not outs:
        L.append("- Output table (X1) needs `output:` in the experiment.")
    L.append("")
    lt = results.get("leak_test")
    if lt:
        L.append(f"Leak test: {lt['files_scanned']} public files scanned, {lt['hits']} hits ({'passed' if lt['passed'] else 'FAILED'}).")
        L.append("")
    return "\n".join(L)


def write_public(dir_: Path, results: Mapping[str, Any], manifest: Mapping[str, Any]) -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    write_canonical(dir_ / "results.json", dict(results))
    write_csv(results, dir_ / "results.csv")
    (dir_ / "report.md").write_text(report_md(results), encoding="utf-8", newline="\n")
    write_canonical(dir_ / "run_manifest.json", dict(manifest))
