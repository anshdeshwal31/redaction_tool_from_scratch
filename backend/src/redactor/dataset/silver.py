"""S1 silver-pass tooling (plan §11 S1, EX-001; procedure in docs/s1_procedure.md).

Writes only under golden_dataset/annotations_raw/claude_silver/. Prints counts and codes only.
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any

from ..core.canonical import read_json, sha256_file, sha256_hex, write_canonical
from ..core.types import BBox, PageExtraction, build_page_text
from ..detectors.baseline import validators as idv
from ..extraction import cache
from ..extraction.render import png_bytes, render_page
from ..paths import docs_dir, golden_dir
from ..taxonomy import ENTITY_TYPES, load_policy, load_taxonomy
from .models import AnnotationSet, CanonicalRegistry, Coverage, Entity, Provenance, Region, RegistryEntity, SilverInfo, TextAnchor
from .register import document_path, load_manifest
from .validate import Report, check_annotation_set

GUIDELINES_VERSION = "0.1.0"
VIEW_DPI = 120
MATTER = "matter_001"


class SilverError(ValueError):
    pass


def silver_dir() -> Path:
    return golden_dir() / "annotations_raw" / "claude_silver"


def work_dir(document_id: str | None = None) -> Path:
    base = silver_dir() / "work"
    return base / document_id if document_id else base


def prompt_sha256() -> str:
    data = (docs_dir() / "annotation_guidelines.md").read_bytes() + b"\n----\n" + (docs_dir() / "s1_procedure.md").read_bytes()
    return sha256_hex(data)


# ------------------------------------------------------------------ packets
def make_packets(document_id: str, *, view_dpi: int = VIEW_DPI) -> dict[str, Any]:
    from ..ingest.metadata import load_metadata
    meta = load_metadata(document_id)
    pages = cache.load_reference_pages(document_id)
    out = work_dir(document_id)
    out.mkdir(parents=True, exist_ok=True)
    images = 0
    for pm in meta.pages:
        pe = pages[pm.page]
        lines = pe.text.split("\n") if pe.text else []
        low = {w.line for w in pe.words if w.conf is not None and w.conf < 50}
        body = [f"# {document_id} page {pm.page}/{meta.page_count} | {pm.classification.content_kind} | "
                f"source {pe.text_source_id} | {len(lines)} lines | page {pm.width_pt}x{pm.height_pt} pt"]
        body += [f"L{i + 1:03d}{'*' if i in low else ''}| {line}" for i, line in enumerate(lines)]
        (out / f"p{pm.page:04d}.txt").write_text("\n".join(body) + "\n", encoding="utf-8", newline="\n")
        if pe.method in ("ocr", "hybrid") or pm.signals.image_count:
            img = render_page(document_path(document_id), pm.page, view_dpi)
            (out / f"p{pm.page:04d}.png").write_bytes(png_bytes(img))
            images += 1
    fields = cache.load_fields(document_id)
    ftxt = []
    for name in sorted(fields):
        ftxt.append(f"## field {name}")
        ftxt += [f"L{i + 1:03d}| {line}" for i, line in enumerate(fields[name].split("\n"))]
    (out / "fields.txt").write_text("\n".join(ftxt) + "\n", encoding="utf-8", newline="\n")
    return {"document_id": document_id, "pages": meta.page_count, "view_images": images, "fields": len(fields)}


# ------------------------------------------------------------------ rotated aux views
# Some scans hold landscape content on a portrait page. The reference OCR (no orientation
# detection) is unreadable there, so the annotator reads an auxiliary OCR of the page turned
# upright. The aux text is never an anchor: entities from it are regions with gold text
# (guidelines §3, "OCR missed the mention entirely"). Boxes are kept in rotated space and only
# the final per-line regions are mapped back to displayed page space.
ROTATIONS = (90, 270)  # clockwise degrees that make the content upright


def aux_tag(rot: int, psm: int | None = None) -> str:
    return f"r{rot}" + (f"p{psm}" if psm else "")


def _aux_path(document_id: str, page: int, tag: str, ext: str) -> Path:
    return work_dir(document_id) / "aux" / f"p{page:04d}.{tag}.{ext}"


def _unrotate_box(b: BBox, rot: int, page_w: float, page_h: float) -> BBox:
    """Map a box from the clockwise-rotated image back to displayed page space."""
    from ..core.coords import unrotate_box
    if rot not in ROTATIONS:
        raise SilverError("unsupported rotation")
    return unrotate_box(b, rot, page_w, page_h)


def make_rotated_views(document_id: str, pages: list[int], rot: int, psm: int | None = None) -> dict[str, Any]:
    """OCR the given pages turned upright with the pinned Tesseract; write aux packets."""
    from PIL import Image

    from ..extraction.base import OcrSettings, ocr_config
    from ..extraction.ocr_tesseract import TesseractConfig, parse_tsv, run_batch
    from ..extraction.render import render_cached
    from ..ingest.metadata import load_metadata
    if rot not in ROTATIONS:
        raise SilverError("unsupported rotation")
    meta = load_metadata(document_id)
    cfg = TesseractConfig.load()
    settings = OcrSettings(dpi=int(ocr_config()["render"]["dpi"]), psm=psm or int(ocr_config()["tesseract"]["psm"]))
    tag = aux_tag(rot, psm)
    aux = work_dir(document_id) / "aux"
    img_dir = aux / f"in_r{rot}"
    img_dir.mkdir(parents=True, exist_ok=True)
    pngs = []
    for page in pages:
        img = Image.open(render_cached(document_id, document_path(document_id), page, settings.dpi))
        img.load()
        turned = img.transpose(Image.Transpose.ROTATE_270 if rot == 90 else Image.Transpose.ROTATE_90)
        out = img_dir / f"p{page:04d}.png"
        out.write_bytes(png_bytes(turned))
        pngs.append(out)
    tsvs = run_batch(cfg, pngs, aux / f"tsv_{tag}", settings)
    lines_total = 0
    for page, png in zip(pages, pngs):
        pm = meta.pages[page - 1]
        text, words = build_page_text(parse_tsv(tsvs[png].read_text(encoding="utf-8"), settings.dpi))
        w_rot, h_rot = (pm.height_pt, pm.width_pt)
        pe = PageExtraction(document_id, page, f"aux.{tag}", "ocr", w_rot, h_rot, words, text,
                            {"rotation_cw": rot, "psm": settings.psm, "tsv_sha256": sha256_file(tsvs[png])})
        write_canonical(_aux_path(document_id, page, tag, "json"), pe.to_dict())
        lines = text.split("\n") if text else []
        low = {w.line for w in words if w.conf is not None and w.conf < 50}
        body = [f"# {document_id} page {page} AUX {tag} (rotated {rot} cw, psm {settings.psm}) | {len(lines)} lines | not an anchor source"]
        body += [f"L{i + 1:03d}{'*' if i in low else ''}| {line}" for i, line in enumerate(lines)]
        _aux_path(document_id, page, tag, "txt").write_text("\n".join(body) + "\n", encoding="utf-8", newline="\n")
        lines_total += len(lines)
    return {"document_id": document_id, "pages": len(pages), "aux": tag, "lines": lines_total}


def _load_aux(document_id: str, page: int, tag: str) -> PageExtraction:
    p = _aux_path(document_id, page, tag, "json")
    if not p.exists():
        raise SilverError("aux view missing")
    return PageExtraction.from_dict(read_json(p))


# ------------------------------------------------------------------ conversion
def _line_starts(text: str) -> list[int]:
    starts = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            starts.append(i + 1)
    return starts


def _boundary_ok(text: str, start: int, end: int) -> bool:
    s = text[start:end]
    if s and s[0].isalnum() and start > 0 and text[start - 1].isalnum():
        return False
    if s and s[-1].isalnum() and end < len(text) and text[end].isalnum():
        return False
    return True


def locate(text: str, surface: str, line: int | None, occurrence: int = 1, *, all_: bool = False) -> list[tuple[int, int]]:
    """Offsets of a surface string. With a line: the n-th occurrence starting on that 1-based line."""
    if not surface:
        raise SilverError("empty surface")
    starts = _line_starts(text)
    lo, hi = 0, len(text) + 1
    if line is not None:
        if line < 1 or line > len(starts):
            raise SilverError("line out of range")
        lo = starts[line - 1]
        hi = starts[line] if line < len(starts) else len(text) + 1
    found = []
    i = text.find(surface, lo)
    while i != -1 and i < hi:
        if _boundary_ok(text, i, i + len(surface)):
            found.append((i, i + len(surface)))
        i = text.find(surface, i + 1)
    if all_:
        if not found:
            raise SilverError("surface not found")
        return found
    if len(found) < occurrence:
        raise SilverError("surface not found at the given line/occurrence")
    return [found[occurrence - 1]]


def regions_for_span(pe: PageExtraction, start: int, end: int) -> list[BBox]:
    """One box per line: word boxes, partial words apportioned by character position."""
    by_line: dict[int, list[BBox]] = {}
    for w in pe.words_in_span(start, end):
        n = max(1, w.end - w.start)
        a = max(start, w.start) - w.start
        b = min(end, w.end) - w.start
        x0 = w.bbox.x0 + w.bbox.width * a / n
        x1 = w.bbox.x0 + w.bbox.width * b / n
        by_line.setdefault(w.line, []).append(BBox(x0, w.bbox.y0, max(x1, x0 + 0.01), w.bbox.y1))
    return [BBox.union_all(boxes) for _, boxes in sorted(by_line.items())]


def _region(b: BBox) -> Region:
    return Region(x0=round(b.x0, 2), y0=round(b.y0, 2), x1=round(b.x1, 2), y1=round(b.y1, 2))


def build_entities(document_id: str, proposals: list[dict[str, Any]]) -> tuple[list[Entity], dict[str, int]]:
    taxonomy = load_taxonomy()
    policy = load_policy()
    pages = cache.load_reference_pages(document_id)
    fields = cache.load_fields(document_id)
    built: list[tuple[tuple, dict[str, Any]]] = []
    problems: dict[str, int] = {}
    for idx, p in enumerate(proposals):
        etype = p["t"]
        if etype not in ENTITY_TYPES:
            raise SilverError(f"proposal {idx}: unknown type")
        attrs = dict(p.get("a") or {})
        role = p.get("role")
        flags = list(p.get("f") or [])
        cid = p.get("cid")
        canonical = f"{MATTER}/{cid}" if cid else None
        base = {"canonical_id": canonical, "entity_type": etype, "role": role, "flags": flags,
                "certainty": p.get("c", "certain"), "notes": p.get("note", "")}
        field = p.get("field")
        if "box" in p:
            # Region given as page fractions: region-only types (no text), or text the OCR missed
            # entirely (gold text required, no anchor; guidelines §3).
            page = int(p["p"])
            pe = pages[page]
            fx0, fy0, fx1, fy1 = p["box"]
            box = BBox(fx0 * pe.width_pt, fy0 * pe.height_pt, fx1 * pe.width_pt, fy1 * pe.height_pt)
            if not taxonomy.region_only(etype) and not p.get("gold"):
                raise SilverError(f"proposal {idx}: a text entity given as a box needs gold text")
            spans = [(None, None, [box], None, page, None)]
        elif "aux" in p:
            # Located in a rotated aux view: regions only, gold text = aux surface unless given.
            page = int(p["p"])
            aux = _load_aux(document_id, page, p["aux"])
            rot = int(aux.engine["rotation_cw"])
            if taxonomy.region_only(etype):
                raise SilverError(f"proposal {idx}: region-only types use a box")
            spans = []
            if "parts" in p:
                # One mention whose pieces sit on non-adjacent aux lines (e.g. a date in a table cell).
                if not p.get("gold"):
                    raise SilverError(f"proposal {idx}: a multi-part mention needs gold text")
                boxes = []
                for part in p["parts"]:
                    (s, e), = locate(aux.text, part[1], int(part[0]), int(part[2]) if len(part) > 2 else 1)
                    boxes += [_unrotate_box(b, rot, pages[page].width_pt, pages[page].height_pt)
                              for b in regions_for_span(aux, s, e)]
                spans.append((None, None, boxes, None, page, None, None))
            else:
                for s, e in locate(aux.text, p["s"], p.get("l"), int(p.get("n", 1)), all_=bool(p.get("all"))):
                    boxes = [_unrotate_box(b, rot, pages[page].width_pt, pages[page].height_pt)
                             for b in regions_for_span(aux, s, e)]
                    spans.append((None, None, boxes, None, page, None, aux.text[s:e]))
            base = {**base, "certainty": p.get("c", "probable")}
        elif field is not None:
            ftext = fields.get(field)
            if ftext is None:
                raise SilverError(f"proposal {idx}: field not present")
            spans = [(s, e, [], ftext[s:e], None, field) for s, e in
                      locate(ftext, p["s"], p.get("l"), int(p.get("n", 1)), all_=bool(p.get("all")))]
        else:
            page = int(p["p"])
            pe = pages[page]
            spans = []
            for s, e in locate(pe.text, p["s"], p.get("l"), int(p.get("n", 1)), all_=bool(p.get("all"))):
                spans.append((s, e, regions_for_span(pe, s, e), pe.text[s:e], page, None))
        for sp in spans:
            s, e, boxes, surface, page, fld = sp[:6]
            gold = p.get("gold") or surface or (sp[6] if len(sp) > 6 else None)
            d = dict(base)
            d["attributes"] = dict(attrs)
            d["flags"] = list(flags)
            if surface is not None and gold != surface and "ocr_degraded" not in d["flags"]:
                d["flags"].append("ocr_degraded")
            kind = taxonomy.validator(etype)
            if kind and gold:
                d["attributes"]["checksum"] = "valid" if idv.validate(kind, gold) else "invalid"
            dec = policy.decide(etype, role, d["attributes"], kind)
            d.update({"text": None if taxonomy.region_only(etype) else gold, "page": page, "field": fld,
                      "regions": [_region(b) for b in boxes], "action": dec.action, "action_source": "policy"})
            if s is not None:
                src = f"field:{fld}" if fld else pages[page].text_source_id
                d["text_anchor"] = TextAnchor(text_source_id=src, start=s, end=e)
            first = boxes[0] if boxes else BBox(0, 0, 0, 0)
            key = (page or 0, fld or "", round(first.y0, 1), round(first.x0, 1), etype, s or 0)
            built.append((key, d))
    built.sort(key=lambda kd: kd[0])
    entities = []
    for n, (_, d) in enumerate(built, 1):
        d["entity_id"] = f"{document_id}.e{n:04d}"
        d["provenance"] = Provenance(annotator="claude_silver", pass_=1, origin="silver")
        entities.append(Entity.model_validate(d))
    for e in entities:
        problems[e.entity_type] = problems.get(e.entity_type, 0) + 1
    return entities, problems


def input_hashes(document_id: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for page, sid in sorted(cache.load_reference(document_id).items()):
        out[f"p{page:04d}.text"] = sha256_file(cache.page_path(document_id, sid, page))
        png = work_dir(document_id) / f"p{page:04d}.png"
        if png.exists():
            out[f"p{page:04d}.view_png"] = sha256_file(png)
    fields = cache.doc_dir(document_id) / "fields.json"
    if fields.exists():
        out["fields"] = sha256_file(fields)
    return out


def silver_info(document_id: str, model_id: str, run_date: str) -> SilverInfo:
    return SilverInfo(model_id=model_id, run_date=run_date, guidelines_version=GUIDELINES_VERSION,
                      prompt_sha256=prompt_sha256(), input_sha256s=input_hashes(document_id))


def load_registry() -> CanonicalRegistry | None:
    p = silver_dir() / "registry" / f"{MATTER}.entities.json"
    return CanonicalRegistry.model_validate(read_json(p)) if p.exists() else None


def build_silver(document_id: str, *, model_id: str, run_date: str | None = None) -> dict[str, Any]:
    run_date = run_date or _dt.date.today().isoformat()
    prop_path = work_dir() / f"{document_id}.proposals.json"
    proposals = read_json(prop_path)
    manifest = {d.document_id: d for d in load_manifest().documents}
    from ..ingest.metadata import load_metadata
    meta = load_metadata(document_id)
    log_entry: dict[str, Any] = {"document_id": document_id, "proposals_sha256": sha256_file(prop_path), "run_date": run_date}
    try:
        entities, counts = build_entities(document_id, proposals["entities"])
        fields_present = sorted(cache.load_fields(document_id))
        ann = AnnotationSet(
            document_id=document_id, document_sha256=manifest[document_id].sha256, annotator="claude_silver",
            pass_=1, mode="silver", revision=1, guidelines_version=GUIDELINES_VERSION, policy_ref=load_policy().ref,
            coverage=Coverage(pages_complete=sorted(proposals.get("pages_complete", [p.page for p in meta.pages])),
                              pages_verified=[], fields_complete=[f for f in fields_present if f in (
                                  "filename", "pdf.title", "pdf.author", "pdf.subject", "pdf.keywords", "xmp")],
                              types_complete=[t for t in ENTITY_TYPES if t != "OTHER"]),
            entities=entities, silver=silver_info(document_id, model_id, run_date))
        report = Report(path=f"{document_id}.ann.json")
        target = silver_dir() / f"{document_id}.ann.json"
        check_annotation_set(ann, report, path=target, registry=load_registry(), metadata=meta)
        log_entry.update({"ok": report.ok, "entities": len(entities), "by_type": dict(sorted(counts.items())),
                          "errors": sorted({i.code for i in report.errors}),
                          "warnings": sorted({i.code for i in report.warnings})})
        if report.ok:
            log_entry["sha256"] = write_canonical(target, ann.dump())
        else:
            log_entry["error_detail"] = [i.as_dict() for i in report.errors][:50]
    except (SilverError, KeyError, ValueError) as exc:
        log_entry.update({"ok": False, "exception": type(exc).__name__, "reason": str(exc) if isinstance(exc, SilverError) else ""})
    with open(work_dir() / "runs.jsonl", "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(log_entry, sort_keys=True) + "\n")
    return log_entry


def build_registry(*, model_id: str, run_date: str | None = None) -> dict[str, Any]:
    run_date = run_date or _dt.date.today().isoformat()
    props = read_json(work_dir() / "registry.proposals.json")
    ents = []
    for e in props["entities"]:
        ents.append(RegistryEntity(canonical_id=f"{MATTER}/{e['cid']}", entity_type=e["t"], role=e.get("role"),
                                   gender=e.get("gender"), gender_evidence=e.get("gender_evidence"),
                                   date_of_birth=e.get("dob"), label=e.get("label", ""), notes=e.get("note", "")))
    ents.sort(key=lambda r: r.canonical_id)
    info = SilverInfo(model_id=model_id, run_date=run_date, guidelines_version=GUIDELINES_VERSION,
                      prompt_sha256=prompt_sha256(), input_sha256s={"registry.proposals": sha256_file(work_dir() / "registry.proposals.json")})
    reg = CanonicalRegistry(matter_id=MATTER, annotator="claude_silver", entities=ents, silver=info)
    sha = write_canonical(silver_dir() / "registry" / f"{MATTER}.entities.json", reg.dump())
    return {"entities": len(ents), "persons": sum(1 for e in ents if e.entity_type == "PERSON"), "sha256": sha[:12]}
