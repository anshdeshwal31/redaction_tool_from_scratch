"""Export of a matter's outputs (plan §4.11, §4.12): fail-closed.

The pipeline run stages, per document, the `redaction.pseudonymized_document` JSON and its private
edit-log sidecar under data/outputs/<matter>/. Export copies them out only when the gate allows it
(every page PASS or REVIEW with a recorded decision, leak scan clean), and only after the final leak
scan has run again on every export artifact: the JSON, the text and Markdown renders and the re-OCR of
the burned PDF pages. Any hit refuses the document. There is no force option.

Public results are counts and hashes; the files themselves are written under exports/<matter>/
(git-ignored), the private sidecars under exports/<matter>/private/.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ..core.canonical import read_json, sha256_hex, write_canonical
from ..paths import exports_dir
from . import pdf as pdfmod
from .export import verify_sha
from .render import render_markdown, render_text


class ExportRefused(RuntimeError):
    def __init__(self, message: str, detail: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.detail = dict(detail or {})


def staged_paths(matter_id: str, doc_id: str) -> tuple[Path, Path]:
    from ..pipeline import outputs_dir
    d = outputs_dir(matter_id)
    return d / f"{doc_id}.export.json", d / "private" / f"{doc_id}.edit_log.private.json"


def artifact_scan(texts: Mapping[str, str], doc_id: str, vault, cfg: Mapping[str, Any], allow: set[str]) -> list[dict[str, Any]]:
    """Final leak scan on export artifacts (names of artifacts -> text). Returns hit locations only."""
    from ..gate import leakscan
    needles = leakscan.vault_needles(vault, cfg)
    hits = []
    for name, text in sorted(texts.items()):
        for h in leakscan.merge_hits(leakscan._scan_text(doc_id, None, name, text, needles, allow, cfg, vault)):
            hits.append({"artifact": name, "start": h.start, "end": h.end, "method": h.method, "kind": h.kind})
    return hits


HASH_KEYS = ("output_sha256", "source_sha256", "config_hash", "detectors", "text_source_id")


def json_strings(doc: Any) -> str:
    """Every string value of the JSON, one per line, except hex digests and fingerprints (a 9-digit run
    inside a SHA-256 would otherwise "validate" as a TFN). Numbers are coordinates and offsets."""
    out: list[str] = []

    def walk(x: Any, key: str = "") -> None:
        if isinstance(x, dict):
            for k in sorted(x):
                if k not in HASH_KEYS:
                    walk(x[k], k)
        elif isinstance(x, list):
            for v in x:
                walk(v, key)
        elif isinstance(x, str):
            out.append(x)
    walk(doc)
    return "\n".join(out)


def allow_from(doc: Mapping[str, Any], vault) -> set[str]:
    from ..gate.leakscan import _n
    allow: set[str] = set()
    for _k, _r, sur in vault.mappings():
        allow.update({sur, _n(sur)})
    pages = {p["page"]: p["text"] for p in doc["pages"]}
    for e in doc["edit_log"]:
        t = pages.get(e["page"], "") if e.get("page") is not None else doc["fields"].get(e.get("field") or "", "")
        s = t[e["new_start"]:e["new_end"]]
        allow.update({s, _n(s)})
    return allow


def export_matter(matter_id: str, *, formats: Sequence[str] = ("json", "text", "md", "pdf"), dpi: int = 300, vault=None,
                  ocr_fn: Callable | None = None, render=None, source_pdf: Callable[[str], Path] | None = None,
                  gate_cfg: Mapping[str, Any] | None = None, out_root: Path | None = None) -> dict[str, Any]:
    import yaml
    from ..dataset.register import document_path
    from ..paths import REPO_ROOT
    from ..pipeline import load_state
    from ..replacement.vault import Vault
    state = load_state(matter_id)
    if state is None:
        raise ExportRefused("no gate run for this matter")
    cfg = (gate_cfg or yaml.safe_load((REPO_ROOT / "config" / "release_gate.v0.1.yaml").read_text(encoding="utf-8"))).get("final_leak_scan", {})
    own = vault is None
    vault = vault or Vault.open(matter_id)
    out_root = (out_root or exports_dir()) / matter_id
    report: dict[str, Any] = {"matter_id": matter_id, "documents": {}}
    try:
        for doc_id in sorted(state["documents"]):
            rec: dict[str, Any] = {}
            report["documents"][doc_id] = rec
            if not state["export_allowed"].get(doc_id):
                rec.update(status="refused", reason="gate_not_clear",
                           pages_not_clear=sum(1 for p in state["pages"] if p["document_id"] == doc_id and p["state"] not in ("PASS", "REVIEW_DECIDED")))
                continue
            sp, priv = staged_paths(matter_id, doc_id)
            if not sp.exists():
                rec.update(status="refused", reason="not_staged")
                continue
            doc = read_json(sp)
            if not verify_sha(doc) or doc["output_sha256"] != state.get("export_sha256", {}).get(doc_id):
                rec.update(status="refused", reason="staged_output_changed")
                continue
            files: dict[str, bytes] = {}
            texts: dict[str, str] = {}
            if "json" in formats:
                from ..core.canonical import canonical_json
                files[f"{doc_id}.pseudonymized.json"] = canonical_json(doc).encode("utf-8")
                texts["json"] = json_strings(doc)
            if "text" in formats:
                texts["text"] = render_text(doc)
                files[f"{doc_id}.txt"] = texts["text"].encode("utf-8")
            if "md" in formats:
                texts["md"] = render_markdown(doc)
                files[f"{doc_id}.md"] = texts["md"].encode("utf-8")
            allow = allow_from(doc, vault)
            box_manifest = None
            if "pdf" in formats:
                regions = read_json(priv).get("regions", []) if priv.exists() else []
                src = (source_pdf or document_path)(doc_id)
                pdf_bytes, images, box_manifest = pdfmod.burn_document(src, doc, regions, dpi=dpi, render=render)
                checks = pdfmod.verify_container(pdf_bytes, doc_id)
                rec["pdf_checks"] = checks
                if not checks["ok"]:
                    rec.update(status="refused", reason="pdf_container_check_failed")
                    continue
                ocr_texts = (ocr_fn or pdfmod.tesseract_ocr)(images, dpi)
                for i, t in enumerate(ocr_texts, 1):
                    texts[f"pdf_reocr.p{i}"] = t
                files[f"{doc_id}.pdf"] = pdf_bytes
            hits = artifact_scan(texts, doc_id, vault, cfg, allow)
            rec["artifact_leak_hits"] = len(hits)
            if hits:
                rec.update(status="refused", reason="artifact_leak_scan", hits=hits)
                continue
            out_root.mkdir(parents=True, exist_ok=True)
            for name, data in sorted(files.items()):
                (out_root / name).write_bytes(data)
            if priv.exists() or box_manifest:
                (out_root / "private").mkdir(parents=True, exist_ok=True)
                if priv.exists():
                    (out_root / "private" / f"{doc_id}.edit_log.private.json").write_bytes(priv.read_bytes())
                if box_manifest:
                    write_canonical(out_root / "private" / f"{doc_id}.boxes.json", box_manifest)
            rec.update(status="exported", files={n: sha256_hex(d) for n, d in sorted(files.items())})
            _record_export(out_root, doc_id, vault.epoch, doc["output_sha256"], sorted(files))
    finally:
        if own:
            vault.close()
    report["exported"] = sum(1 for r in report["documents"].values() if r["status"] == "exported")
    report["refused"] = sum(1 for r in report["documents"].values() if r["status"] == "refused")
    return report


# ---------------------------------------------------------------- stale exports (plan §4.9.5 epochs)
def _record_path(out_root: Path) -> Path:
    return out_root / "private" / "export_record.json"


def _record_export(out_root: Path, doc_id: str, epoch: int, output_sha256: str, files: Sequence[str]) -> None:
    p = _record_path(out_root)
    data = read_json(p) if p.exists() else {"schema": "redactor.export_record", "documents": {}}
    data["documents"][doc_id] = {"vault_epoch": int(epoch), "output_sha256": output_sha256, "files": list(files)}
    write_canonical(p, data)


def export_status(matter_id: str, current_epoch: int, out_root: Path | None = None) -> dict[str, Any]:
    """Which exported documents were produced under an older vault epoch. A re-issued surrogate bumps the
    epoch (plan §4.9.5), so those exports no longer match the vault and must be re-exported."""
    p = _record_path((out_root or exports_dir()) / matter_id)
    docs = (read_json(p) if p.exists() else {"documents": {}})["documents"]
    out = {d: {"exported_epoch": r["vault_epoch"], "stale": int(r["vault_epoch"]) < int(current_epoch)} for d, r in sorted(docs.items())}
    return {"matter_id": matter_id, "vault_epoch": int(current_epoch), "documents": out,
            "stale": sorted(d for d, r in out.items() if r["stale"])}
