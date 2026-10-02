"""Rule-based linker `rule_based@0.1.0` (plan §4.1 S3, §4.9.1).

Runs over every document of a matter before any replacement (two-pass processing, §4.9.5).
- PERSON: full names group by (surname, first initial); a single-token mention joins the one group whose
  surname (or, failing that, given name) it equals, else it forms its own group. Honorifics just before
  a mention are collected as gender evidence for the group.
- ORGANIZATION: normalised name without legal suffixes. Identifiers: digits only. Others: normalised text.
Group IDs are SHA-256 based; every list is sorted, so the result does not depend on input order.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Mapping, Sequence

from ..core.canonical import sha256_hex
from ..core.types import DocumentText, EntitySpan
from ..detectors.baseline import validators as v
from ..replacement.keys import norm
from ..replacement.names import honorific_before, parse

VERSION = "rule_based@0.1.0"
ORG_SUFFIX = re.compile(r"\b(pty\.?\s*ltd\.?|limited|ltd\.?|inc\.?|incorporated|group)\s*$", re.I)
IDENT = {"CLAIM_NUMBER", "COURT_FILE_NUMBER", "POLICY_NUMBER", "TFN", "ABN", "ACN", "PASSPORT", "DRIVER_LICENCE",
         "VEHICLE_REGISTRATION", "CENTRELINK_CRN", "ACCOUNT_NUMBER", "MEDICARE", "PROVIDER_NUMBER", "AHPRA_REGISTRATION",
         "IHI", "MEDICAL_RECORD_NUMBER", "DVA_NUMBER", "PHONE"}


def span_key(s: EntitySpan) -> tuple:
    return (s.document_id, -1 if s.page is None else s.page, s.field or "", s.start, s.end, s.entity_type)


@dataclass
class Group:
    group_id: str
    entity_type: str
    members: list[tuple] = field(default_factory=list)
    honorifics: list[str] = field(default_factory=list)
    surnames: set[str] = field(default_factory=set)
    given: set[str] = field(default_factory=set)


@dataclass
class Linking:
    version: str
    group_of: dict[tuple, str]
    groups: dict[str, Group]

    def group(self, s: EntitySpan) -> Group | None:
        gid = self.group_of.get(span_key(s))
        return self.groups.get(gid) if gid else None


def _text_of(docs: Mapping[str, DocumentText], s: EntitySpan) -> str:
    d = docs[s.document_id]
    if s.page is not None:
        return d.pages[s.page].text
    return d.fields.get(s.field or "", "")


def _gid(etype: str, key: str) -> str:
    return f"{etype.lower()}:{sha256_hex(etype + '|' + key)[:12]}"


def link(docs: Sequence[DocumentText], spans: Mapping[str, Sequence[EntitySpan]]) -> Linking:
    by_doc = {d.document_id: d for d in docs}
    allspans = sorted((s for d in sorted(spans) for s in spans[d]), key=EntitySpan.sort_key)
    group_of: dict[tuple, str] = {}
    groups: dict[str, Group] = {}

    def add(gid: str, etype: str, s: EntitySpan) -> Group:
        g = groups.setdefault(gid, Group(gid, etype))
        g.members.append(span_key(s))
        group_of[span_key(s)] = gid
        return g

    persons = [s for s in allspans if s.entity_type == "PERSON"]
    known_sur: set[str] = set()
    known_giv: set[str] = set()
    parsed = {}
    for s in persons:
        toks = parse(s.text, honorific=honorific_before(_text_of(by_doc, s), s.start))
        parsed[span_key(s)] = toks
        if len(toks) >= 2:
            known_sur.update(norm(t.text.rstrip(".")) for t in toks if t.role == "surname")
            known_giv.update(norm(t.text.rstrip(".")) for t in toks if t.role == "given")
    full_groups: dict[str, list[str]] = defaultdict(list)       # surname -> group ids
    given_groups: dict[str, list[str]] = defaultdict(list)
    singles = []
    from ..replacement.pools import _lexicon
    lexicon = _lexicon()
    for s in persons:
        toks = parse(s.text, known_surnames=known_sur, known_given=known_giv,
                     honorific=honorific_before(_text_of(by_doc, s), s.start), lexicon=lexicon)
        parsed[span_key(s)] = toks
        sur = [norm(t.text.rstrip(".")) for t in toks if t.role == "surname"]
        giv = [t.text.rstrip(".") for t in toks if t.role in ("given", "initial")]
        if sur and giv:
            key = sur[0] + "|" + norm(giv[0])[:1]
            gid = _gid("PERSON", key)
            g = add(gid, "PERSON", s)
            g.surnames.add(sur[0])
            g.given.update(norm(x) for x in giv if len(x) > 1)
            if gid not in full_groups[sur[0]]:
                full_groups[sur[0]].append(gid)
            for x in giv:
                if len(x) > 1 and gid not in given_groups[norm(x)]:
                    given_groups[norm(x)].append(gid)
        else:
            singles.append(s)
    for s in singles:
        toks = parsed[span_key(s)]
        tok = norm(toks[0].text.rstrip(".")) if toks else norm(s.text)
        role = toks[0].role if toks else "surname"
        cands = full_groups.get(tok, []) if role == "surname" else given_groups.get(tok, [])
        if not cands:
            cands = given_groups.get(tok, []) or full_groups.get(tok, [])
        gid = sorted(cands)[0] if len(cands) == 1 else _gid("PERSON", f"single|{role}|{tok}")
        g = add(gid, "PERSON", s)
        (g.surnames if role == "surname" else g.given).add(tok)
    for s in persons:
        h = honorific_before(_text_of(by_doc, s), s.start)
        if h:
            groups[group_of[span_key(s)]].honorifics.append(h)
    for s in allspans:
        if s.entity_type == "PERSON":
            continue
        if s.entity_type == "ORGANIZATION":
            key = ORG_SUFFIX.sub("", norm(s.text)).strip()
        elif s.entity_type in IDENT:
            key = v.digits_only(s.text) or norm(s.text)
        else:
            key = norm(s.text)
        add(_gid(s.entity_type, key), s.entity_type, s)
    for g in groups.values():
        g.members.sort()
        g.honorifics.sort()
    return Linking(VERSION, group_of, dict(sorted(groups.items())))
