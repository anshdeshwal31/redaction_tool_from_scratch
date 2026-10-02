"""Pseudonymisation of a matter: S3 linking -> S4 policy -> S5 replacement (plan §4.1, §4.9).

Two passes: linking, name evidence and DOB reference dates are collected over every document of the
matter first; only then are surrogates issued, in a fixed sorted order (so collision counters do not
depend on document order). Every replacement is an `Edit` (start, end, replacement, group, rule,
strategy), which gives the offset map evaluation needs and the export's audit trail (§4.9.6).
REVIEW spans are left unchanged and listed for the review queue (counted as leaked, pessimistic).
"""

from __future__ import annotations

import datetime as dt
import json
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence

from ..core.canonical import sha256_hex
from ..core.types import DocumentText, EntitySpan
from ..linking.linker import Linking, link, span_key
from ..taxonomy import PROTECT_ACTIONS, Decision, Policy, Taxonomy
from . import contact, dob as dobmod, identifiers as ids
from .keys import norm
from .names import NameMapper, gender_of, honorific_before, parse
from .pools import Pools, load_pools
from .vault import Vault, VaultError

VERSION = "surrogate@0.1.0"
REF_ROLES = {"date_of_injury", "examination", "report", "claim"}
PRIORITY = {"REDACT": 0, "SYNTHETIC": 1, "REVIEW": 2, "KEEP": 3}
_PROTECT_ROLE = {"ORGANIZATION": "employer", "LOCATION": None}


@dataclass(frozen=True)
class Edit:
    document_id: str
    page: int | None
    field: str | None
    text_source_id: str
    start: int
    end: int
    replacement: str
    entity_type: str
    action: str
    strategy: str | None
    rule: str
    group_id: str | None
    identity: str = ""          # JSON of name components (PERSON) for consistency metrics

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DocResult:
    document_id: str
    pages: dict[int, str]
    fields: dict[str, str]
    edits: list[Edit] = field(default_factory=list)
    reviews: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class MatterResult:
    documents: dict[str, DocResult]
    linking: Linking
    dob_issued: list[dict[str, Any]]
    summary: dict[str, Any]


def _text(doc: DocumentText, s: EntitySpan) -> str:
    return doc.pages[s.page].text if s.page is not None else doc.fields.get(s.field or "", "")


def _resolve(items: list[tuple[EntitySpan, Any]]) -> list[tuple[EntitySpan, Any]]:
    """Non-overlapping spans per text; protection wins, then length, then position, then type."""
    kept: list[tuple[EntitySpan, Any]] = []
    for s, d in sorted(items, key=lambda x: (PRIORITY.get(x[1].action, 9), -(x[0].end - x[0].start), x[0].start, x[0].entity_type)):
        if not any(k.text_source_id == s.text_source_id and k.page == s.page and k.field == s.field and k.start < s.end and s.start < k.end
                   for k, _ in kept):
            kept.append((s, d))
    return sorted(kept, key=lambda x: x[0].sort_key())


def _sentence(text: str, pos: int) -> tuple[int, int]:
    a = max(text.rfind(".", 0, pos), text.rfind("\n\n", 0, pos))
    b = text.find(".", pos)
    return (a + 1, b if b != -1 else len(text))


def _email_hint(value: str, mapper: NameMapper, pools: Pools, surnames: set[str], given: set[str],
                genders: Mapping[str, str]) -> str | None:
    """A local part built from a person's name keeps that shape with the person's surrogate components."""
    local = value.split("@", 1)[0]
    parts = re.split(r"([._\-])", local)
    known = {p.casefold() for p in parts if p and p not in "._-"} & (surnames | given)
    if not known:
        return None
    out = []
    for p in parts:
        c = p.casefold()
        if p in "._-" or not p:
            out.append(p)
        elif c in surnames:
            out.append(mapper.surname(p).lower())
        elif c in given:
            out.append(mapper.given(p, genders.get(c, "unknown")).lower())
        else:
            out.append(f"x{len(p)}")   # an unrecognised token is not carried over
    return "".join(out)


def pseudonymize(docs: Sequence[DocumentText], spans: Mapping[str, Sequence[EntitySpan]], *, policy: Policy,
                 taxonomy: Taxonomy, vault: Vault, registry_gender: Mapping[str, str] | None = None) -> MatterResult:
    by_doc = {d.document_id: d for d in sorted(docs, key=lambda d: d.document_id)}
    texts = [pe.text for d in by_doc.values() for pe in d.pages.values()] + [t for d in by_doc.values() for t in d.fields.values()]
    pools = load_pools(texts)
    forbidden = ids.identifier_tokens(texts)
    key = vault.matter_key
    mapper = NameMapper(key, pools, vault)
    linking = link(list(by_doc.values()), spans)

    # ---- decisions and overlap resolution
    decided: dict[str, list[tuple[EntitySpan, Any]]] = {}
    for doc_id in sorted(by_doc):
        items = []
        for s in spans.get(doc_id, []):
            if taxonomy.region_only(s.entity_type):
                continue
            attrs = {k: v for k, v in s.attributes}
            dec = policy.decide(s.entity_type, s.attr("role"), attrs, taxonomy.validator(s.entity_type))
            ov = s.attr("override_action")   # a recorded reviewer decision (gate review queue)
            if ov:
                base = policy.decide(s.entity_type, s.attr("role") or _PROTECT_ROLE.get(s.entity_type), attrs, taxonomy.validator(s.entity_type))
                dec = Decision(ov, base.strategy if base.action == ov else None, base.token if base.action == ov else None,
                               "override", "review_decision")
            items.append((s, dec))
        decided[doc_id] = _resolve(items)

    # ---- pass 1: evidence over the whole matter
    person_tokens: set[str] = set()
    known_sur: set[str] = set()
    known_giv: set[str] = set()
    for g in linking.groups.values():
        if g.entity_type == "PERSON":
            known_sur |= g.surnames
            known_giv |= g.given
    person_tokens = {t for t in known_sur | known_giv if len(t) >= 3}
    group_gender: dict[str, str] = {}
    for gid, g in linking.groups.items():
        if g.entity_type == "PERSON":
            reg = (registry_gender or {}).get(gid)
            group_gender[gid] = gender_of(sorted(g.given), g.honorifics, pools, reg)
    given_gender: dict[str, str] = {}
    for gid, g in linking.groups.items():
        if g.entity_type == "PERSON":
            for gv in g.given:
                given_gender.setdefault(gv, group_gender[gid])
    refs_by_doc: dict[str, list[dt.date]] = defaultdict(list)
    for doc_id, items in decided.items():
        doc = by_doc[doc_id]
        ages = [(s.page, _sentence(_text(doc, s), s.start)) for s, _ in items if s.entity_type == "AGE"]
        for s, _ in items:
            if s.entity_type != "DATE":
                continue
            f = dobmod.parse(s.text)
            if f is None or f.kind in ("year", "month_year"):
                continue
            in_age_sentence = any(p == s.page and a <= s.start <= b for p, (a, b) in ages)
            if s.attr("date_role") in REF_ROLES or in_age_sentence:
                refs_by_doc[doc_id].append(f.date)
    dob_docs: dict[str, set[str]] = defaultdict(set)
    for doc_id, items in decided.items():
        for s, d in items:
            if s.entity_type == "DATE_OF_BIRTH" and d.action in PROTECT_ACTIONS:
                f = dobmod.parse(s.text)
                if f is not None and f.kind not in ("year", "month_year"):
                    dob_docs[f.date.isoformat()].add(doc_id)
    dob_sur: dict[str, str | None] = {}
    dob_issued = []
    reviews_global: list[dict[str, Any]] = []
    for iso in sorted(dob_docs):
        b = dt.date.fromisoformat(iso)
        canonical = "dob:" + sha256_hex(iso)[:12]
        refs = sorted({r for d in sorted(dob_docs[iso]) for r in refs_by_doc[d]})
        frozen = vault.get_dob(canonical)
        min_window = int(policy.defaults.get("DATE_OF_BIRTH", {}).get("min_window_days", 14))
        if frozen is None:
            sur = dobmod.issue(key, canonical, b, refs, min_window)
            vault.put_dob(canonical, iso, sur.isoformat() if sur else None, [r.isoformat() for r in refs])
        else:
            sur = dt.date.fromisoformat(frozen[1]) if frozen[1] else None
            if sur is not None and dobmod.conflicts(b, sur, refs):
                reviews_global.append({"reason": "DOB_AGE_CONFLICT", "kind": "DATE_OF_BIRTH", "documents": sorted(dob_docs[iso])})
                vault.add_review("DATE_OF_BIRTH", "DOB_AGE_CONFLICT", {"canonical": canonical})
        dob_sur[iso] = sur.isoformat() if sur else None
        dob_issued.append({"canonical": canonical, "refs": len(refs), "window_ok": sur is not None,
                           "age_preserved": sur is not None and not dobmod.conflicts(b, sur, refs),
                           "changed": sur is not None and sur != b, "year_kept": sur is None or sur.year == b.year})

    # ---- pass 2: surrogates in a fixed sorted order, then edits
    order = sorted(((s, d, doc_id) for doc_id, items in decided.items() for s, d in items),
                   key=lambda x: (x[0].entity_type, norm(x[0].text), x[2], x[0].sort_key()))
    replacement: dict[tuple, tuple[str, str]] = {}
    reviews: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s, d, doc_id in order:
        k = span_key(s)
        doc = by_doc[doc_id]
        g = linking.group(s)
        if d.action == "KEEP":
            continue
        if d.action == "REVIEW":
            reviews[doc_id].append({"reason": "policy_review", "entity_type": s.entity_type, "page": s.page, "field": s.field,
                                    "start": s.start, "end": s.end})
            continue
        ident = ""
        if d.action == "REDACT":
            out = d.token or f"[{s.entity_type}]"
            vault.add_redaction(s.entity_type, s.text)
        elif s.entity_type == "PERSON":
            text = _text(doc, s)
            hon = honorific_before(text, s.start)
            toks = parse(s.text, known_surnames=known_sur, known_given=known_giv, honorific=hon, lexicon=pools.first_name_gender)
            gender = group_gender.get(g.group_id, "unknown") if g else "unknown"
            out, comps = mapper.replace(s.text, toks, gender)
            ident = json.dumps(comps, sort_keys=True)
        elif s.entity_type == "DATE_OF_BIRTH":
            f = dobmod.parse(s.text)
            if f is not None and f.kind == "year":
                continue
            sur = dob_sur.get(f.date.isoformat()) if f is not None and f.kind != "month_year" else None
            if f is not None and f.kind == "month_year":
                cands = [iso for iso in dob_sur if iso[:7] == f.date.isoformat()[:7] and dob_sur[iso]]
                sur = dob_sur[cands[0]] if cands else None
            if sur is None:
                out = dobmod.TOKEN
                vault.add_redaction("DATE_OF_BIRTH", s.text)
                reviews[doc_id].append({"reason": "dob_token_fallback", "entity_type": s.entity_type, "page": s.page,
                                        "field": s.field, "start": s.start, "end": s.end})
            else:
                out = dobmod.render(f, dt.date.fromisoformat(sur))
        elif s.entity_type == "ORGANIZATION":
            out = contact.organisation(key, s.text, pools, vault)
        elif s.entity_type == "ADDRESS":
            out = contact.address(key, s.text, pools, vault, {k2: v2 for k2, v2 in s.attributes})
        elif s.entity_type == "LOCATION":
            out = contact.location(key, s.text, s.attr("granularity"), pools, vault)
        elif s.entity_type == "PHONE":
            out = vault.get("PHONE", s.text) or contact.phone(key, s.text, pools, vault.issued("PHONE"))
        elif s.entity_type == "EMAIL":
            out = vault.get("EMAIL", s.text) or contact.email(key, s.text, pools, _email_hint(
                s.text, mapper, pools, {x for x in known_sur if len(x) >= 3}, {x for x in known_giv if len(x) >= 3}, given_gender),
                vault.issued("EMAIL"))
        elif s.entity_type == "URL":
            out = contact.url(key, s.text, pools)
        else:   # identifiers and anything else that the policy says to replace
            out = vault.get(s.entity_type, s.text) or ids.surrogate(key, s.entity_type, s.text, taken=vault.issued(s.entity_type),
                                                                    forbidden_digits=forbidden)
        if s.entity_type in ("ADDRESS", "LOCATION", "ORGANIZATION") and d.action == "SYNTHETIC":
            out = mirror_case(s.text, out)
        if d.action == "SYNTHETIC":
            direct = s.entity_type in ("PHONE", "EMAIL") or s.entity_type in policy.identifier_types
            try:
                vault.put(s.entity_type if direct else f"surface:{s.entity_type}", s.text, out)
            except VaultError:
                if direct:
                    raise
                # the same surface in a different role (e.g. a postcode vs a street number): first mapping stays
        replacement[k] = (out, ident)
    vault.commit()

    results: dict[str, DocResult] = {}
    for doc_id, doc in by_doc.items():
        edits: list[Edit] = []
        for s, d in decided[doc_id]:
            k = span_key(s)
            if k not in replacement:
                continue
            out, ident = replacement[k]
            g = linking.group(s)
            edits.append(Edit(doc_id, s.page, s.field, s.text_source_id, s.start, s.end, out, s.entity_type, d.action,
                              d.strategy or d.token, d.rule, g.group_id if g else None, ident))
        pages = {p: _apply(pe.text, [e for e in edits if e.page == p]) for p, pe in sorted(doc.pages.items())}
        fields = {f: _apply(t, [e for e in edits if e.field == f]) for f, t in sorted(doc.fields.items())}
        rv = sorted(reviews[doc_id], key=lambda r: (r["page"] or 0, r["field"] or "", r["start"]))
        rv += [r for r in reviews_global if doc_id in r.get("documents", [])]
        results[doc_id] = DocResult(doc_id, pages, fields, edits, rv)
    summary = {
        "replacement": VERSION, "linker": linking.version,
        "edits": sum(len(r.edits) for r in results.values()),
        "edits_by_type": dict(sorted(_count(e.entity_type for r in results.values() for e in r.edits).items())),
        "reviews": sum(len(r.reviews) for r in results.values()),
        "dob": {"issued": sum(1 for x in dob_issued if x["window_ok"]), "token_fallback": sum(1 for x in dob_issued if not x["window_ok"]),
                "conflicts": sum(1 for r in reviews_global if r["reason"] == "DOB_AGE_CONFLICT")},
        "pool_entries_removed_as_in_matter_text": pools.removed,
    }
    return MatterResult(results, linking, dob_issued, summary)


def _apply(text: str, edits: list[Edit]) -> str:
    out = text
    for e in sorted(edits, key=lambda e: -e.start):
        out = out[:e.start] + e.replacement + out[e.end:]
    return out


def _count(items) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for i in items:
        out[i] += 1
    return out


def mirror_case(source: str, out: str) -> str:
    """An ALL-CAPS (or all-lowercase) source keeps that casing in its surrogate: a Title-case address
    inside a capitalised form block is a tell, and re-identification restores casing from the surrogate."""
    letters = [c for c in source if c.isalpha()]
    if len(letters) > 1 and all(c.isupper() for c in letters):
        return out.upper()
    if letters and all(c.islower() for c in letters):
        return out.lower()
    return out


def output_digest(result: MatterResult) -> str:
    """Canonical digest of every output text and edit (replacement determinism, §4.7)."""
    payload = {d: {"pages": r.pages, "fields": r.fields, "edits": [e.to_dict() for e in r.edits], "reviews": r.reviews}
               for d, r in sorted(result.documents.items())}
    return sha256_hex(json.dumps(payload, sort_keys=True, ensure_ascii=False))
