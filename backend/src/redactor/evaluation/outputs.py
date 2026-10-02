"""E9: outputs and re-identification (plan §4.5 "Outputs", "Re-identification", §12).

Outputs
- JSON schema validity; render determinism (JSON, text, Markdown rebuilt and compared byte for byte).
- Edit-log completeness: rebuilding the output from the original text and the edit log (original
  offsets from the private sidecar, new offsets from the export) reproduces the output exactly, edits
  are disjoint, and every original's keyed hash matches; i.e. each changed range is covered by exactly
  one edit.
- No original text in the exported JSON outside the page texts: originals of the edited spans are
  searched in the JSON with the page texts and fields blanked (expect 0); the page texts themselves
  are covered by the residual metrics (E5).
- PDF burn-in (optional): gold_protect region area covered, collateral share, container checks (no
  text layer, fonts or metadata), writer byte determinism, and residual PII by re-OCR of the burned pages.

Re-identification
- Round trip: re-identifying each output page restores every SYNTHETIC edit exactly (pages that hold
  REDACT tokens are compared outside the tokens).
- Simulated downstream answers: templated sentences with surrogate forms (full name, title + surname,
  possessive, initial + surname, "SURNAME, Given", UPPER): restoration, ambiguity and wrong-restoration
  rates (target 0 wrong).
- One-way check: REDACT tokens are never restored; the vault holds no plaintext of REDACT-class values.
All results are counts and rates.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any, Callable, Mapping, Sequence

from ..core.canonical import canonical_json, sha256_hex
from ..core.types import BBox, DocumentText
from ..reidentify.core import TOKEN_RE, build_index, reidentify
from ..replacement.keys import hmac_hex, norm
from ..reporting import leak
from ..taxonomy import PROTECT_ACTIONS
from ..output import export as ox
from ..output.models import validate_document
from ..output.render import render_markdown, render_text


def _ratio(a: int, b: int) -> float | None:
    return round(a / b, 4) if b else None


def build_documents(matter, docs: Mapping[str, DocumentText], vault, matter_id: str) -> dict[str, tuple[dict, dict]]:
    gids = [e.group_id for r in matter.documents.values() for e in r.edits if e.group_id]
    ids = ox.opaque_ids(gids, matter_id)
    out = {}
    for d, r in sorted(matter.documents.items()):
        out[d] = ox.build(d, matter_id, "", docs[d], r, ids=ids, matter_key=vault.matter_key, page_classes={},
                          gate_pages=None, leak_hits=0, pipeline={"evaluation": True})
    return out


def edit_log_complete(doc: Mapping[str, Any], private: Mapping[str, Any], source: DocumentText, matter_key: bytes) -> bool:
    priv = {e["edit_id"]: e for e in private["edits"]}
    by_page: dict[Any, list[tuple[dict, dict]]] = defaultdict(list)
    for e in doc["edit_log"]:
        p = priv.get(e["edit_id"])
        if p is None:
            return False
        by_page[("p", e["page"]) if e["page"] is not None else ("f", e.get("field"))].append((e, p))
    texts = {("p", pg["page"]): pg["text"] for pg in doc["pages"]}
    texts.update({("f", f): t for f, t in doc["fields"].items()})
    for key, out_text in texts.items():
        orig = source.pages[key[1]].text if key[0] == "p" else source.fields.get(key[1], "")
        pairs = sorted(by_page.get(key, []), key=lambda x: x[1]["orig_start"])
        rebuilt, pos, last_new = [], 0, 0
        for e, p in pairs:
            if p["orig_start"] < pos or e["new_start"] < last_new:
                return False   # overlapping edits
            if hmac_hex(matter_key, "ORIG", orig[p["orig_start"]:p["orig_end"]]) != p["orig_hmac"]:
                return False
            rebuilt.append(orig[pos:p["orig_start"]])
            rebuilt.append(out_text[e["new_start"]:e["new_end"]])
            pos, last_new = p["orig_end"], e["new_end"]
        rebuilt.append(orig[pos:])
        if "".join(rebuilt) != out_text:
            return False
    return True


def outputs_metrics(built: Mapping[str, tuple[dict, dict]], sources: Mapping[str, DocumentText], matter, vault,
                    rebuild: Callable[[], Mapping[str, tuple[dict, dict]]]) -> dict[str, Any]:
    valid = complete = 0
    leaks_meta = 0
    again = rebuild()
    det_same = 0
    for d, (doc, priv) in sorted(built.items()):
        valid += validate_document(doc)
        complete += edit_log_complete(doc, priv, sources[d], vault.matter_key)
        o2 = again[d][0]
        det_same += (canonical_json(doc) == canonical_json(o2) and render_text(doc) == render_text(o2)
                     and render_markdown(doc) == render_markdown(o2))
        # originals of edited spans must not appear anywhere in the JSON outside the page texts/fields
        blank = json.loads(canonical_json(doc))
        for pg in blank["pages"]:
            pg["text"] = ""
        blank["fields"] = {k: "" for k in blank["fields"]}
        surfaces = []
        for e in matter.documents[d].edits:
            if e.action in PROTECT_ACTIONS:
                src = sources[d].pages[e.page].text if e.page is not None else sources[d].fields.get(e.field or "", "")
                surfaces.append(leak.Surface(src[e.start:e.end], e.entity_type))
        needles = leak.build_needles(surfaces)
        needles["fuzzy"] = []
        leaks_meta += len(leak.scan_text(canonical_json(blank), needles))
    n = len(built)
    return {"documents": n, "schema_valid": valid, "render_deterministic": det_same, "edit_log_complete": complete,
            "original_text_hits_outside_page_text": leaks_meta,
            "edits": sum(len(doc["edit_log"]) for doc, _ in built.values())}


def roundtrip_metrics(built: Mapping[str, tuple[dict, dict]], sources: Mapping[str, DocumentText], matter, vault,
                      pool_names: set[str]) -> dict[str, Any]:
    idx = build_index(vault, pool_names=pool_names)
    syn = ok = 0
    pages = pages_ok = 0
    tokens_restored = 0
    for d, (doc, priv) in sorted(built.items()):
        r = matter.documents[d]
        for pg in doc["pages"]:
            p = pg["page"]
            restored, rep = reidentify(pg["text"], idx)
            orig = sources[d].pages[p].text
            edits = [e for e in r.edits if e.page == p]
            # tokens must survive restoration unchanged
            tokens_restored += max(0, len(TOKEN_RE.findall(pg["text"])) - len(TOKEN_RE.findall(restored)))
            # per edit: restoring the replacement alone gives the original surface
            for e in edits:
                if e.action != "SYNTHETIC":
                    continue
                syn += 1
                got, _ = reidentify(e.replacement, idx)
                ok += got == orig[e.start:e.end]
            pages += 1
            expected = orig
            for e in sorted((e for e in edits if e.action != "SYNTHETIC"), key=lambda e: -e.start):
                expected = expected[:e.start] + e.replacement + expected[e.end:]
            pages_ok += restored == expected
    return {"synthetic_edits": syn, "restored_exactly": ok, "roundtrip_rate": _ratio(ok, syn),
            "pages": pages, "pages_restored_exactly": pages_ok, "page_roundtrip_rate": _ratio(pages_ok, pages),
            "redact_tokens_restored": tokens_restored}


TEMPLATES = (
    ("full", "{G} {S} attended the review."),
    ("title_surname", "The report by Dr {S} was filed."),
    ("possessive", "{S}'s claim was lodged."),
    ("initial_surname", "{I}. {S} signed it."),
    ("surname_given", "{SU}, {G} is listed."),
    ("upper", "{GU} {SU} appeared."),
)


def downstream_metrics(matter, vault, pool_names: set[str]) -> dict[str, Any]:
    """Simulated downstream answers per person identity issued in the matter."""
    idx = build_index(vault, pool_names=pool_names)
    people: dict[tuple[str, str], None] = {}
    for r in matter.documents.values():
        for e in r.edits:
            if e.entity_type == "PERSON" and e.action == "SYNTHETIC" and e.identity:
                ident = json.loads(e.identity)
                if ident.get("given") and ident.get("surname"):
                    people[(ident["given"], ident["surname"])] = None
    rev: dict[tuple[str, str], set[str]] = defaultdict(set)
    for kind, real, sur in vault.mappings():
        if kind == "surname" or kind.startswith("given:"):
            rev[(kind.split(":")[0], norm(sur))].add(norm(real))
    by_form: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for g_sur, s_sur in sorted(people):
        real_s = sorted(rev.get(("surname", s_sur), set()))
        real_g = sorted(rev.get(("given", g_sur), set()))
        if len(real_s) != 1 or len(real_g) != 1:
            for form, _ in TEMPLATES:
                by_form[form]["ambiguous_identity"] += 1
            continue
        rs, rg = real_s[0], real_g[0]
        tc = lambda x: " ".join("-".join(q[:1].upper() + q[1:] for q in w.split("-")) for w in x.split())   # noqa: E731
        sur_vals = {"G": tc(g_sur), "S": tc(s_sur), "I": tc(g_sur)[:1], "SU": s_sur.upper(), "GU": g_sur.upper()}
        real_vals = {"G": tc(rg), "S": tc(rs), "I": tc(rg)[:1], "SU": rs.upper(), "GU": rg.upper()}
        for form, tpl in TEMPLATES:
            text, expect = tpl.format(**sur_vals), tpl.format(**real_vals)
            got, rep = reidentify(text, idx)
            c = by_form[form]
            c["n"] += 1
            c[_classify(text, expect, got)] += 1
            if rep.to_dict()["ambiguous_total"]:
                c["with_ambiguous_token"] += 1
    tot = defaultdict(int)
    for c in by_form.values():
        for k, v in c.items():
            tot[k] += v
    return {"identities": len(people), "by_form": {k: dict(sorted(v.items())) for k, v in sorted(by_form.items())},
            "restoration_rate": _ratio(tot["restored"] + tot["restored_case_variant"], tot["n"]),
            "partial_rate": _ratio(tot["partial"], tot["n"]), "unrestored_rate": _ratio(tot["unrestored"], tot["n"]),
            "ambiguity_rate": _ratio(tot["with_ambiguous_token"], tot["n"]),
            "wrong_restoration_rate": _ratio(tot["wrong"], tot["n"]), "n": tot["n"]}


def _classify(text: str, expect: str, got: str) -> str:
    """restored | restored_case_variant (same letters, e.g. McKenzie vs Mckenzie) | unrestored (nothing
    changed) | partial (some surrogate tokens left in place, the safe direction) | wrong (a token became a
    value other than the expected real one)."""
    if got == expect:
        return "restored"
    if norm(got) == norm(expect):
        return "restored_case_variant"
    if got == text:
        return "unrestored"
    tw, ew, gw = text.split(), expect.split(), got.split()
    if len(tw) == len(ew) == len(gw):
        for t, e, g in zip(tw, ew, gw):
            if norm(g) != norm(e) and g != t:
                return "wrong"
        return "partial"
    return "wrong"


def one_way_metrics(vault, taxonomy) -> dict[str, Any]:
    kinds = {k for k, _r, _s in vault.mappings()}
    redact_types = sorted(t for t in ("TFN", "NATIONALITY") if t in kinds or f"surface:{t}" in kinds)
    redactions = vault.conn.execute("SELECT COUNT(*) FROM redaction").fetchone()[0]
    return {"redact_types_with_plaintext_in_vault": len(redact_types), "redaction_hashes": redactions}


def pdf_metrics(built: Mapping[str, tuple[dict, dict]], gold_regions: Mapping[str, Mapping[int, list[BBox]]], *, source_pdf,
                dpi: int, render=None, ocr_fn=None, surfaces: Mapping[str, Sequence[leak.Surface]] | None = None) -> dict[str, Any]:
    from ..output import pdf as pdfmod
    boxes_all, gold_all = [], []
    checks_ok = det_ok = 0
    reocr_hits = 0
    reocr_pages = 0
    for d, (doc, _priv) in sorted(built.items()):
        pdf1, images, man = pdfmod.burn_document(source_pdf(d), doc, [], dpi=dpi, render=render)
        pdf2, _, _ = pdfmod.burn_document(source_pdf(d), doc, [], dpi=dpi, render=render)
        det_ok += sha256_hex(pdf1) == sha256_hex(pdf2)
        checks_ok += pdfmod.verify_container(pdf1, d)["ok"]
        for pg in man["pages"]:
            boxes_all.append([BBox.from_dict(b) for b in pg["boxes_pt"]])
            gold_all.append(gold_regions.get(d, {}).get(pg["page"], []))
        if ocr_fn is not None and surfaces is not None:
            needles = leak.build_needles(surfaces.get(d, []))
            for t in ocr_fn(images, dpi):
                reocr_pages += 1
                reocr_hits += len(leak.scan_text(t, needles))
    area = pdfmod.area_metrics(boxes_all, gold_all)
    return {"documents": len(built), "container_checks_passed": checks_ok, "writer_byte_deterministic": det_ok,
            **area, "reocr_pages": reocr_pages if ocr_fn else None, "reocr_gold_hits": reocr_hits if ocr_fn else None}
