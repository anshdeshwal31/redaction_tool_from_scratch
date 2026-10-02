"""E5: privacy, preservation and consistency of the pseudonymised output (plan §4.5).

Inputs: gold mentions projected onto the text sources the replacement ran on, and the replacement
result (edits, output texts, DOB issuance, linking). Outputs are counts and rates only.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any, Mapping, Sequence

from ..core.types import DocumentText
from ..detectors.baseline import validators as v
from ..replacement import contact
from ..replacement.engine import Edit, MatterResult
from ..replacement.names import honorific_before
from ..replacement.pools import Pools
from ..reporting import leak
from ..taxonomy import PROTECT_ACTIONS, Taxonomy
from .gold import GoldMention
from .iaa import b_cubed
from .metrics import prf, ratio
from .ocr_metrics import surface_recovery

TOKENS = {"TFN": "[TFN]", "NATIONALITY": "[NATIONALITY]"}


def _key(doc: str, page, field) -> tuple:
    return (doc, -1 if page is None else page, field or "")


def _cover(start: int, end: int, ivs: Sequence[tuple[int, int]]) -> int:
    iv = sorted((max(start, a), min(end, b)) for a, b in ivs if a < end and start < b)
    total, cs, ce = 0, None, None
    for a, b in iv:
        if ce is None or a > ce:
            if ce is not None:
                total += ce - cs
            cs, ce = a, b
        else:
            ce = max(ce, b)
    if ce is not None:
        total += ce - cs
    return total


def evaluate_outputs(gold: Mapping[str, Sequence[GoldMention]], result: MatterResult, docs: Mapping[str, DocumentText], *,
                     taxonomy: Taxonomy, pools: Pools, registry_gender: Mapping[str, str] | None = None) -> dict[str, Any]:
    edits_at: dict[tuple, list[Edit]] = defaultdict(list)
    review_at: dict[tuple, list[tuple[int, int]]] = defaultdict(list)
    for doc_id, r in result.documents.items():
        for e in r.edits:
            edits_at[_key(doc_id, e.page, e.field)].append(e)
        for rv in r.reviews:
            if "start" in rv:
                review_at[_key(doc_id, rv.get("page"), rv.get("field"))].append((rv["start"], rv["end"]))
    out: dict[str, Any] = {}

    # ---- residual PII (mention level), pessimistic and with review
    per_doc: dict[str, dict[str, int]] = defaultdict(lambda: {"protect": 0, "residual": 0, "critical_residual": 0})
    res_p = res_r = n_prot = crit = crit_res = 0
    for doc_id, ms in sorted(gold.items()):
        for m in ms:
            if m.excluded is not None or not m.protect:
                continue
            n_prot += 1
            per_doc[doc_id]["protect"] += 1
            crit += m.critical
            if not m.projected:
                res_p += 1
                res_r += 1
                per_doc[doc_id]["residual"] += 1
                crit_res += m.critical
                per_doc[doc_id]["critical_residual"] += m.critical
                continue
            k = _key(doc_id, m.page, m.field)
            prot_iv = [(e.start, e.end) for e in edits_at[k] if e.action in PROTECT_ACTIONS]
            full = _cover(m.start, m.end, prot_iv) >= m.end - m.start
            full_r = _cover(m.start, m.end, prot_iv + review_at[k]) >= m.end - m.start
            if not full:
                res_p += 1
                per_doc[doc_id]["residual"] += 1
                crit_res += m.critical
                per_doc[doc_id]["critical_residual"] += m.critical
            res_r += not full_r
    out["residual"] = {"gold_protect": n_prot, "pessimistic": res_p, "with_review": res_r, "rate_pessimistic": ratio(res_p, n_prot),
                       "rate_with_review": ratio(res_r, n_prot), "critical": {"total": crit, "residual": crit_res},
                       "by_document": {d: dict(v2) for d, v2 in sorted(per_doc.items())}}

    # ---- leak scan of the output text (gold surfaces of the matter)
    needles = leak.build_needles([leak.Surface(m.text, m.entity_type) for ms in gold.values() for m in ms
                                  if m.text and m.protect and m.excluded is None])
    # allow-list: surrogates the vault issued (a surrogate can equal a real name OCR never saw, §4.11)
    issued: set[str] = set()
    for r in result.documents.values():
        for e in r.edits:
            issued.add(leak._norm(e.replacement))
            issued.update(leak._norm(x) for x in json.loads(e.identity or "{}").values())
            issued.update(t for t in re.findall(r"[a-z][a-z'\-]+", leak._norm(e.replacement)))
    allow = 0
    for k2 in ("exact", "fuzzy"):
        kept_n = [n for n in needles[k2] if n not in issued]
        allow += len(needles[k2]) - len(kept_n)
        needles[k2] = kept_n
    tok_kept = [t for t in needles["tokens"] if t.casefold() not in issued]
    allow += len(needles["tokens"]) - len(tok_kept)
    needles["tokens"] = tok_kept
    hits_by_doc: dict[str, dict[str, int]] = {}
    for doc_id, r in sorted(result.documents.items()):
        text = "\n".join(list(r.pages.values()) + list(r.fields.values()))
        hits = leak.scan_text(text, needles)
        c: dict[str, int] = defaultdict(int)
        for meth, _ in hits:
            c[meth] += 1
        hits_by_doc[doc_id] = dict(sorted(c.items()))
    out["leak_scan"] = {"hits": sum(sum(v2.values()) for v2 in hits_by_doc.values()), "by_document": hits_by_doc,
                        "needles": {k: len(v2) for k, v2 in needles.items()}, "allow_listed_surrogates": allow}

    # ---- preservation: KEEP mentions untouched, non-PII characters unchanged
    keep_n = keep_ok = 0
    for doc_id, ms in gold.items():
        for m in ms:
            if m.excluded is None and m.projected and m.action == "KEEP":
                keep_n += 1
                k = _key(doc_id, m.page, m.field)
                keep_ok += _cover(m.start, m.end, [(e.start, e.end) for e in edits_at[k]]) == 0
    by_type_keep: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for doc_id, ms in gold.items():
        for m in ms:
            if m.excluded is None and m.projected and m.action == "KEEP":
                k = _key(doc_id, m.page, m.field)
                by_type_keep[m.entity_type][1] += 1
                by_type_keep[m.entity_type][0] += _cover(m.start, m.end, [(e.start, e.end) for e in edits_at[k]]) == 0
    nonpii_total = nonpii_changed = 0
    for doc_id, d in docs.items():
        for p, pe in d.pages.items():
            k = _key(doc_id, p, None)
            gp = [(m.start, m.end) for m in gold.get(doc_id, []) if m.page == p and m.projected and (m.protect or m.excluded is not None)]
            protected_chars = _cover(0, len(pe.text), gp)
            edit_iv = [(e.start, e.end) for e in edits_at[k]]
            changed_all = _cover(0, len(pe.text), edit_iv)
            changed_in_gold = sum(_cover(a, b, edit_iv) for a, b in _merge(gp))
            nonpii_total += len(pe.text) - protected_chars
            nonpii_changed += changed_all - changed_in_gold
    out["preservation"] = {"keep_mentions": keep_n, "keep_preserved": keep_ok, "keep_rate": ratio(keep_ok, keep_n),
                           "keep_by_type": {t: ratio(a, b) for t, (a, b) in sorted(by_type_keep.items())},
                           "non_pii_chars": nonpii_total, "over_redacted_chars": nonpii_changed,
                           "non_pii_char_preservation": ratio(nonpii_total - nonpii_changed, nonpii_total)}
    dobs = result.dob_issued
    issued = [d for d in dobs if d["window_ok"]]
    out["dob"] = {"persons": len(dobs), "issued": len(issued), "token_fallback": len(dobs) - len(issued),
                  "age_preserved": ratio(sum(1 for d in issued if d["age_preserved"]), len(issued)),
                  "changed": ratio(sum(1 for d in issued if d["changed"]), len(issued)),
                  "year_kept": ratio(sum(1 for d in issued if d["year_kept"]), len(issued))}

    # ---- REDACT-class types
    red: dict[str, Any] = {}
    for t, tok in TOKENS.items():
        gm = [(doc_id, m) for doc_id, ms in gold.items() for m in ms if m.entity_type == t and m.excluded is None and m.projected]
        ok = 0
        for doc_id, m in gm:
            es = [e for e in edits_at[_key(doc_id, m.page, m.field)] if e.start < m.end and m.start < e.end]
            ok += bool(es) and all(e.replacement == tok for e in es) and _cover(m.start, m.end, [(e.start, e.end) for e in es]) >= m.end - m.start
        false_red = 0
        for k, es in edits_at.items():
            for e in es:
                if e.replacement == tok:
                    golds = [m for m in gold.get(k[0], []) if m.entity_type == t and m.projected and _key(k[0], m.page, m.field) == k
                             and m.start < e.end and e.start < m.end]
                    false_red += not golds
        red[t] = {"gold": len(gm), "redacted": ok, "recall": ratio(ok, len(gm)), "false_redactions": false_red}
    out["redact_class"] = red

    # ---- identifier safety
    src_text = " ".join(pe.text for d in docs.values() for pe in d.pages.values())
    src_digits = {v.digits_only(x) for x in re.findall(r"\d[\d \-/]{3,}\d", src_text)}
    chk = [e for es in edits_at.values() for e in es if e.entity_type in ("MEDICARE", "ABN", "ACN", "PROVIDER_NUMBER", "IHI")
           and e.action == "SYNTHETIC"]
    invalid = sum(1 for e in chk if not v.validate({"MEDICARE": "medicare", "ABN": "abn", "ACN": "acn", "PROVIDER_NUMBER": "provider_number",
                                                     "IHI": "ihi"}[e.entity_type], e.replacement))
    phones = [e for es in edits_at.values() for e in es if e.entity_type == "PHONE" and e.action == "SYNTHETIC"]
    emails = [e for es in edits_at.values() for e in es if e.entity_type == "EMAIL" and e.action == "SYNTHETIC"]
    ident_edits = [e for es in edits_at.values() for e in es if e.action == "SYNTHETIC" and v.digits_only(e.replacement)
                   and e.entity_type not in ("PERSON", "ORGANIZATION", "DATE_OF_BIRTH", "ADDRESS", "LOCATION")]
    clash = sum(1 for e in ident_edits if len(v.digits_only(e.replacement)) >= 5 and v.digits_only(e.replacement) in src_digits)
    out["identifier_safety"] = {
        "checksummed_surrogates": len(chk), "checksum_invalid_share": ratio(invalid, len(chk)),
        "phones": len(phones), "phones_reserved_share": ratio(sum(1 for e in phones if contact.is_reserved_phone(e.replacement, pools)), len(phones)),
        "emails": len(emails), "emails_reserved_share": ratio(sum(1 for e in emails if e.replacement.rsplit("@", 1)[-1].lower() in pools.email_domains), len(emails)),
        "surrogates_in_source_text": clash}

    # ---- consistency (PERSON)
    ident_of: dict[str, dict[str, str]] = {}
    mention_ident: list[tuple[str, str, dict[str, str], str | None]] = []   # (doc, canonical, identity, linker group)
    unrecovered = 0
    for doc_id, ms in gold.items():
        for m in ms:
            if m.entity_type != "PERSON" or m.excluded is not None or not m.projected or not m.canonical_id:
                continue
            src = docs[doc_id].pages[m.page].text[m.start:m.end] if m.page is not None else docs[doc_id].fields.get(m.field or "", "")[m.start:m.end]
            if surface_recovery(m.text, src) not in ("exact", "near"):
                unrecovered += 1   # OCR lost or garbled the name: consistency is not measurable on it
                continue
            es = [e for e in edits_at[_key(doc_id, m.page, m.field)] if e.entity_type == "PERSON" and e.start < m.end and m.start < e.end]
            if not es:
                continue
            ident = {}
            for e in es:
                ident.update(json.loads(e.identity or "{}"))
            mention_ident.append((doc_id, m.canonical_id, ident, es[0].group_id))
    by_can: dict[str, list[dict[str, str]]] = defaultdict(list)
    docs_of: dict[str, set[str]] = defaultdict(set)
    for doc_id, can, ident, _ in mention_ident:
        by_can[can].append(ident)
        docs_of[can].add(doc_id)
    multi = [c for c, ids in by_can.items() if len(ids) >= 2]
    consistent = 0
    full_ident: dict[str, tuple[str, str]] = {}
    for c, ids in by_can.items():
        surs = {i["surname"] for i in ids if "surname" in i}
        givs = {i["given"] for i in ids if "given" in i}
        if c in multi:
            consistent += len(surs) <= 1 and len(givs) <= 1
        if len(surs) == 1 and len(givs) == 1:
            full_ident[c] = (next(iter(surs)), next(iter(givs)))
    collisions = sum(n - 1 for n in _count(full_ident.values()).values() if n > 1)
    cross = [c for c in multi if len(docs_of[c]) >= 2]
    cross_ok = sum(1 for c in cross if len({i.get("surname") for i in by_can[c] if "surname" in i}) <= 1
                   and len({i.get("given") for i in by_can[c] if "given" in i}) <= 1)
    items = [(can, grp or f"none:{i}") for i, (_, can, _, grp) in enumerate(mention_ident)]
    tp = fp = fn = 0
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            same_g = items[i][0] == items[j][0]
            same_p = items[i][1] == items[j][1]
            tp += same_g and same_p
            fp += same_p and not same_g
            fn += same_g and not same_p
    gender_ok = gender_n = 0
    for can, ids in by_can.items():
        g = (registry_gender or {}).get(can)
        if g not in ("female", "male"):
            continue
        for i in ids:
            if "given" in i:
                gender_n += 1
                pool_g = "female" if i["given"] in {x.casefold() for x in pools.female} else ("male" if i["given"] in {x.casefold() for x in pools.male} else "unisex")
                gender_ok += pool_g == g
    mr_female = 0
    for doc_id, r in result.documents.items():
        for e in r.edits:
            if e.entity_type != "PERSON" or e.page is None:
                continue
            given = json.loads(e.identity or "{}").get("given")
            if given and given in {x.casefold() for x in pools.female}:
                src = docs[doc_id].pages[e.page].text
                if honorific_before(src, e.start) in ("Mr", "Master", "Mstr", "Sir"):
                    mr_female += 1
    out["consistency"] = {
        "persons_with_replaced_mentions": len(by_can), "with_2plus_mentions": len(multi),
        "mentions_skipped_unrecovered_surface": unrecovered,
        "alias_consistency": ratio(consistent, len(multi)), "collisions": collisions,
        "cross_document_entities": len(cross), "cross_document_consistency": ratio(cross_ok, len(cross)),
        "grouping_pairwise": prf(tp, fp, fn), "grouping_b3": b_cubed(items),
        "gender_preservation": ratio(gender_ok, gender_n), "gender_checked": gender_n, "male_honorific_with_female_name": mr_female,
    }
    return out


def _merge(iv: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for a, b in sorted(iv):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _count(items) -> dict:
    out: dict = defaultdict(int)
    for i in items:
        out[i] += 1
    return out
