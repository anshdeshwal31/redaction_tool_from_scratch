"""Final leak scan on the pseudonymised output (plan §4.11).

1. Vault surfaces: every real value and form the vault holds (names and name components of 3+ chars,
   identifiers digits-only, addresses, phones, emails, DOB forms), normalised; exact and fuzzy
   (edit distance <= 1 for 6+ chars, with common OCR confusions). REDACT values: by keyed hash.
2. Checksum-valid identifiers: any digit sequence that validates as TFN, ABN, ACN, Medicare, IHI or a
   provider number (surrogates are invalid by construction, so a valid hit is a real identifier).
3. Detector re-run (baseline): any protect-type hit that is not a known surrogate or token.
A hit means BLOCKED. Hits carry locations and methods, never the matched text.
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from rapidfuzz.distance import Levenshtein

from ..core.types import DocumentText, EntitySpan, PageExtraction
from ..detectors.baseline import validators as v
from ..replacement import dob as dobmod
from ..replacement.engine import MatterResult
from ..replacement.vault import Vault
from ..taxonomy import PROTECT_ACTIONS, Policy, Taxonomy

CONFUSE = str.maketrans({"0": "o", "1": "l", "i": "l", "|": "l", "5": "s", "8": "b"})   # common OCR confusions
TOKENS = re.compile(r"\[(TFN|NATIONALITY|DATE_OF_BIRTH|SIGNATURE|PHOTO|[A-Z_]+)\]")


@dataclass(frozen=True)
class Hit:
    document_id: str
    page: int | None
    field: str | None
    start: int
    end: int
    method: str      # vault_exact | vault_fuzzy | redacted_hash | checksum_valid | detector
    kind: str

    def to_public(self) -> dict[str, Any]:
        return {"document_id": self.document_id, "page": self.page, "field": self.field, "start": self.start, "end": self.end,
                "method": self.method, "kind": self.kind}


def _n(t: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", t).split()).casefold()


def _dob_forms(iso: str) -> list[str]:
    d = dt.date.fromisoformat(iso)
    forms = {f"{d.day:02d}/{d.month:02d}/{d.year}", f"{d.day}/{d.month}/{d.year}", f"{d.day:02d}.{d.month:02d}.{d.year}",
             f"{d.day:02d}-{d.month:02d}-{d.year}", f"{d.day:02d}/{d.month:02d}/{d.year % 100:02d}"}
    for kind in ("named_dmy", "named_mdy"):
        for style in ("full", "abbr"):
            for pad in (True, False):
                forms.add(dobmod.render(dobmod.DateForm(kind, d, month_style=style, day_pad=pad), d))
    return sorted(forms)


def vault_needles(vault: Vault, cfg: Mapping[str, Any]) -> dict[str, list[tuple[str, str]]]:
    min_comp = int(cfg.get("name_component_min_len", 3))
    exact: set[tuple[str, str]] = set()
    digits: set[tuple[str, str]] = set()
    names: set[str] = set()
    for kind, real, _sur in vault.mappings():
        base = kind.split(":", 1)[-1] if kind.startswith("surface:") else kind
        if kind == "surname" or kind.startswith("given:"):
            if len(real) >= min_comp:
                names.add(real.strip())
            continue
        if kind == "surface:PERSON":
            names.add(real.strip())
            continue
        if kind.startswith("suburb:") or kind in ("street", "org"):
            exact.add((_n(real), kind.split(":")[0]))
            continue
        d = v.digits_only(real)
        if base in ("PHONE",) or (len(d) >= 6 and sum(c.isdigit() for c in real) >= 0.6 * len(real.replace(" ", ""))):
            if len(d) >= 6:
                digits.add((d, base))
        if len(_n(real)) >= 4:
            exact.add((_n(real), base))
    for (iso,) in vault.conn.execute("SELECT canonical FROM dob"):
        got = vault.get_dob(iso)
        if got:
            for f in _dob_forms(got[0]):
                exact.add((_n(f), "DATE_OF_BIRTH"))
    return {"exact": sorted(exact), "digits": sorted(digits), "names": sorted(names)}


def _scan_text(doc_id: str, page: int | None, field: str | None, text: str, needles, allow: set[str], cfg, vault: Vault) -> list[Hit]:
    hits: list[Hit] = []
    raw = text.casefold()   # positions refer to the output text
    for n, kind in needles["exact"]:
        if n in allow:
            continue
        for m in re.finditer(r"(?<![0-9a-z])" + re.escape(n) + r"(?![0-9a-z])", raw):
            hits.append(Hit(doc_id, page, field, m.start(), m.end(), "vault_exact", kind))
    # name components: Title case or capitals only (a name that is also a common word, "mark", "low",
    # would otherwise flood the scan); still exact and whole-word
    for nm in needles.get("names", []):
        if _n(nm) in allow:
            continue
        title = " ".join(w[:1].upper() + w[1:].lower() for w in nm.split())
        forms = {title, nm.upper()} | ({nm} if nm != nm.lower() else set())   # vault keys are casefolded
        for form in sorted(forms):
            for m in re.finditer(r"(?<![0-9A-Za-z])" + re.escape(form) + r"(?![0-9A-Za-z])", text):
                hits.append(Hit(doc_id, page, field, m.start(), m.end(), "vault_name", "PERSON"))
    min_f = int(cfg.get("fuzzy_min_len", 6))
    max_e = int(cfg.get("fuzzy_max_edits", 1))
    words = [(m.start(), m.end(), m.group(0)) for m in re.finditer(r"\S+", raw)]
    fuzzy_needles = list(needles["exact"]) + [(_n(nm), "PERSON") for nm in needles.get("names", [])]
    for n, kind in fuzzy_needles:
        if len(n) < min_f or n in allow:
            continue
        k = len(n.split())
        nc = n.translate(CONFUSE)
        for i in range(0, max(0, len(words) - k + 1)):
            s, e = words[i][0], words[i + k - 1][1]
            cand = raw[s:e].strip(".,;:()\"'")
            if abs(len(cand) - len(n)) > max_e or cand == n:
                continue
            if Levenshtein.distance(cand.translate(CONFUSE), nc) <= max_e:
                hits.append(Hit(doc_id, page, field, s, e, "vault_fuzzy", kind))
    for m in re.finditer(r"\d[\d \-/.]{4,}\d", text):
        d = v.digits_only(m.group(0))
        for nd, kind in needles["digits"]:
            if nd in d:
                hits.append(Hit(doc_id, page, field, m.start(), m.end(), "vault_digits", kind))
        if vault.is_redacted_value("TFN", m.group(0)) or vault.is_redacted_value("TFN", d):
            hits.append(Hit(doc_id, page, field, m.start(), m.end(), "redacted_hash", "TFN"))
        names = v.any_valid(m.group(0))
        if names and m.group(0) not in allow:
            hits.append(Hit(doc_id, page, field, m.start(), m.end(), "checksum_valid", names[0]))
    for m in re.finditer(r"\b\d{6}[0-9A-Za-z]{2}\b", text):
        if v.provider_number(m.group(0)) and m.group(0) not in allow:
            hits.append(Hit(doc_id, page, field, m.start(), m.end(), "checksum_valid", "provider_number"))
    return hits


def allow_list(vault: Vault, result: MatterResult) -> set[str]:
    allow: set[str] = set()
    for kind, _real, sur in vault.mappings():
        allow.add(sur)
        allow.add(_n(sur))
        allow.update(t for t in re.findall(r"[a-z][a-z'\-]+", _n(sur)))
    for r in result.documents.values():
        for e in r.edits:
            allow.add(e.replacement)
            allow.add(_n(e.replacement))
    return allow


def scan(result: MatterResult, vault: Vault, *, cfg: Mapping[str, Any], policy: Policy, taxonomy: Taxonomy,
         detector=None, source_ids: Mapping[str, Mapping[int, str]] | None = None) -> list[Hit]:
    needles = vault_needles(vault, cfg)
    allow = allow_list(vault, result)
    hits: list[Hit] = []
    for doc_id, r in sorted(result.documents.items()):
        for p, text in sorted(r.pages.items()):
            hits += _scan_text(doc_id, p, None, text, needles, allow, cfg, vault)
        for f, text in sorted(r.fields.items()):
            hits += _scan_text(doc_id, None, f, text, needles, allow, cfg, vault)
    if detector is not None:
        hits += detector_rerun(result, detector, allow, policy, taxonomy)
    return merge_hits(hits)


METHOD_PRIORITY = ("checksum_valid", "redacted_hash", "vault_digits", "vault_exact", "vault_name", "vault_fuzzy", "detector")


def merge_hits(hits: Sequence[Hit]) -> list[Hit]:
    """One hit per leaked location: overlapping hits (a full name and its components, an exact and a
    fuzzy match) merge into their union, labelled with the most specific method."""
    rank = {m: i for i, m in enumerate(METHOD_PRIORITY)}
    groups: dict[tuple[str, int, str], list[Hit]] = {}
    for h in hits:
        groups.setdefault((h.document_id, h.page if h.page is not None else -1, h.field or ""), []).append(h)
    out: list[Hit] = []
    for key in sorted(groups):
        cur: list[Hit] = []
        for h in sorted(groups[key], key=lambda h: (h.start, -h.end, rank.get(h.method, 99), h.kind)):
            if cur and h.start < max(c.end for c in cur):
                cur.append(h)
                continue
            if cur:
                out.append(_union(cur, rank))
            cur = [h]
        if cur:
            out.append(_union(cur, rank))
    return out


def _union(group: Sequence[Hit], rank: Mapping[str, int]) -> Hit:
    best = min(group, key=lambda h: (rank.get(h.method, 99), h.start, -h.end, h.kind))
    return Hit(best.document_id, best.page, best.field, min(h.start for h in group), max(h.end for h in group), best.method, best.kind)


def detector_rerun(result: MatterResult, detector, allow: set[str], policy: Policy, taxonomy: Taxonomy) -> list[Hit]:
    from ..core.types import build_page_text, RawWord, BBox
    from ..detectors.base import run_detector
    docs = []
    for doc_id, r in sorted(result.documents.items()):
        pages = {}
        for p, text in r.pages.items():
            raw = []
            for li, line in enumerate(text.split("\n")):
                for w in line.split(" "):
                    if w:
                        raw.append(RawWord(w, BBox(0, 0, 1, 1), None, 0, li))
            t, words = build_page_text(raw)
            pages[p] = PageExtraction(doc_id, p, "output", "text_layer", 1.0, 1.0, words, t, {})
        docs.append(DocumentText(doc_id, pages, dict(r.fields)))
    out: list[Hit] = []
    for doc_id, spans in run_detector(detector, docs).items():
        for s in spans:
            action = policy.decide(s.entity_type, s.attr("role"), {k: v2 for k, v2 in s.attributes}, taxonomy.validator(s.entity_type)).action
            if action not in PROTECT_ACTIONS:
                continue
            t = _n(s.text)
            if t in allow or TOKENS.fullmatch(s.text.strip()):
                continue
            toks = re.findall(r"[a-z][a-z'\-]+", t)
            if s.entity_type == "PERSON" and toks and all(x in allow for x in toks):
                continue
            if s.entity_type in ("DATE_OF_BIRTH",):
                continue   # surrogate DOBs are allowed by design; real DOB forms are in the vault needles
            out.append(Hit(doc_id, s.page, s.field, s.start, s.end, "detector", s.entity_type))
    return out
