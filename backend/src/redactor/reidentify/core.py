"""Reverse index and restoration (plan §4.12.4).

- The index holds every surrogate form the vault issued (whole surfaces, identifiers, phones, emails,
  addresses, organisations) plus the component maps (surname, given names, suburbs, streets), so a form
  that was never issued still resolves by components ("Dr Jones" -> "Dr Smith").
- Matching is deterministic: one alternation sorted longest first, on word boundaries; an exact-case
  form wins, otherwise the match is case-insensitive and the casing of the match is applied to the real
  value (UPPER, Title, lower). Possessives survive because "'s" lies outside the match.
- An initial ("P." or "P") directly before a restored surname goes through the inverse of the matter's
  letter permutation; any other initial is left alone.
- Safe failure: a surrogate form with more than one real value is ambiguous and left unchanged; tokens
  that look like surrogates (pool names) but are not in the vault are reported as unmatched. REDACT
  tokens ([TFN], [NATIONALITY], [DATE_OF_BIRTH]) are never restored: the vault has no plaintext for them.
- The report has counts per type only, never text.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from ..replacement.keys import norm
from ..replacement.vault import Vault

TOKEN_RE = re.compile(r"\[(?:TFN|NATIONALITY|DATE_OF_BIRTH|SIGNATURE|PHOTO|REDACTED|[A-Z_]{2,})\]")
COMPONENT_KINDS = ("surname", "given")


@dataclass(frozen=True)
class Entry:
    real: str
    kind: str          # vault kind, e.g. surface:PERSON, surname, given:female, EMAIL
    component: bool    # True for component maps (real value stored casefolded)


@dataclass
class Index:
    forms: dict[str, list[Entry]]           # casefolded surrogate form -> entries
    exact: dict[str, list[Entry]]           # surrogate form as issued -> entries
    initials: dict[str, dict[str, set[str]]]   # surrogate surname -> surrogate initial -> real initials
    surnames: set[str]                      # casefolded surrogate surnames
    pool_names: set[str] = field(default_factory=set)
    pattern: re.Pattern | None = None


def _type_of(kind: str) -> str:
    if kind.startswith("surface:"):
        return kind.split(":", 1)[1]
    if kind in ("surname",) or kind.startswith("given:"):
        return "PERSON"
    if kind.startswith("suburb:") or kind == "street":
        return "ADDRESS"
    if kind == "org":
        return "ORGANIZATION"
    return kind


def build_index(vault: Vault, *, pool_names: set[str] | None = None) -> Index:
    forms: dict[str, list[Entry]] = defaultdict(list)
    exact: dict[str, list[Entry]] = defaultdict(list)
    surnames: set[str] = set()
    for kind, real, sur in vault.mappings():
        if not sur or TOKEN_RE.fullmatch(sur.strip()):
            continue            # one-way tokens are never restored
        comp = kind in ("surname", "street", "org") or kind.startswith(("given:", "suburb:"))   # stored casefolded
        e = Entry(real, kind, comp)
        forms[norm(sur)].append(e)
        exact[sur].append(e)
        if kind == "surname":
            surnames.add(norm(sur))
    for real_iso, sur_iso in _dob_pairs(vault):
        for rf, sf in dob_form_pairs(real_iso, sur_iso):
            e = Entry(rf, "DATE_OF_BIRTH", False)
            forms[norm(sf)].append(e)
            exact[sf].append(e)
    keys = sorted(forms, key=lambda f: (-len(f), f))
    pat = re.compile(r"(?<![0-9A-Za-z])(" + "|".join(re.escape(k) for k in keys) + r")(?![0-9A-Za-z])", re.IGNORECASE) if keys else None
    return Index(dict(forms), dict(exact), _initials(vault, surnames), surnames, {norm(x) for x in (pool_names or set())}, pat)


def _initials(vault: Vault, surnames: set[str]) -> dict[str, dict[str, set[str]]]:
    """Which real initial an initial before a surrogate surname stands for, learned from the full-name
    surfaces the vault issued with a surrogate given name ("Peter Brown" -> initial P). Issued forms with
    a permuted initial ("Q. Brown") are restored whole by their surface mapping; a bare permuted initial
    is not learned, because family members share a surname. An initial with more than one candidate,
    or none, is not restored."""
    given: dict[str, set[str]] = defaultdict(set)
    for kind, real, sur in vault.mappings():
        if kind.startswith("given:"):
            given[norm(sur)].add(norm(real))
    out: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for kind, _real, sur in vault.mappings():
        if kind != "surface:PERSON":
            continue
        toks = [t.rstrip(".") for t in re.findall(r"[A-Za-z][A-Za-z'\-]*\.?", sur)]
        sns = [norm(t) for t in toks if norm(t) in surnames]
        for sn in sns:
            for t in toks:
                if norm(t) == sn:
                    continue
                for rg in given.get(norm(t), ()):
                    out[sn][t[:1].upper()].add(rg[:1].upper())
    return {k: dict(v) for k, v in out.items()}


def _dob_pairs(vault: Vault) -> list[tuple[str, str]]:
    out = []
    for (canonical,) in vault.conn.execute("SELECT canonical FROM dob ORDER BY canonical"):
        got = vault.get_dob(canonical)
        if got and got[1]:
            out.append((got[0], got[1]))
    return out


def dob_form_pairs(real_iso: str, sur_iso: str) -> list[tuple[str, str]]:
    """Full-date forms of a surrogate DOB paired with the same form of the real DOB. Partial dates
    (month and year, year) are not restored: they are not unique to the DOB."""
    import datetime as dt
    from ..replacement import dob as dobmod
    r, s = dt.date.fromisoformat(real_iso), dt.date.fromisoformat(sur_iso)
    forms = []
    for sep in ("/", ".", "-"):
        for widths in ((2, 2, 4), (1, 1, 4), (2, 2, 2), (1, 1, 2)):
            forms.append(dobmod.DateForm("numeric", s, sep, widths))
    for kind in ("named_dmy", "named_mdy"):
        for style in ("full", "abbr"):
            for pad in (True, False):
                for ordn in (False, True):
                    for caps in (False, True):
                        forms.append(dobmod.DateForm(kind, s, month_style=style, day_pad=pad, ordinal=ordn, caps=caps))
    pairs = {(dobmod.render(f, r), dobmod.render(f, s)) for f in forms}
    return sorted(pairs)


def _case_word(model: str, real: str) -> str:
    letters = [c for c in model if c.isalpha()]
    if len(letters) > 1 and all(c.isupper() for c in letters):
        return real.upper()
    if letters and all(c.islower() for c in letters):
        return real.lower()
    if model[:1].isupper():
        rl = [c for c in real if c.isalpha()]
        if rl and (all(c.isupper() for c in rl) or all(c.islower() for c in rl)):
            return " ".join("-".join(q[:1].upper() + q[1:].lower() for q in w.split("-")) for w in real.split(" "))
        return real                     # a mixed-case real keeps its own casing (McKenzie, "Hospital of ...")
    return real


def _apply_case(model: str, real: str) -> str:
    """Casing of the matched surrogate text applied to the real value, word by word when both have the
    same number of words ("BROWN, Peter" -> "SMITH, John"), otherwise as a whole."""
    mw, rw = model.split(" "), real.split(" ")
    if len(mw) == len(rw) > 1:
        return " ".join(_case_word(m, r) for m, r in zip(mw, rw))
    return _case_word(model, real)

def _resolve(idx: Index, matched: str) -> tuple[str | None, str, str]:
    """(real text or None, type, status) with status restored | ambiguous."""
    # an exact-case issued form is judged on its own entries (the forward mapping issued exactly that
    # string); otherwise over every case variant
    cands = idx.exact.get(matched) or idx.forms.get(norm(matched), [])
    exact = set(map(id, idx.exact.get(matched, [])))
    reals = sorted({norm(e.real) if e.component else e.real for e in cands})
    kinds = sorted({_type_of(e.kind) for e in cands})
    typ = kinds[0] if len(kinds) == 1 else "MIXED"
    if len({norm(r) for r in reals}) != 1:
        return None, typ, "ambiguous"
    # prefer an entry that kept the original casing (a whole surface) over a casefolded component; the
    # casing of the matched text then decides (UPPER, lower, Title; mixed-case reals keep their own)
    e = sorted(cands, key=lambda x: (id(x) not in exact, x.component, x.kind, x.real))[0]
    return _apply_case(matched, norm(e.real) if e.component else e.real), typ, "restored"


@dataclass
class Report:
    restored: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    ambiguous: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    initials_restored: int = 0
    initials_unresolved: int = 0
    one_way_tokens: int = 0
    unmatched_surrogate_like: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"restored": dict(sorted(self.restored.items())), "restored_total": sum(self.restored.values()),
                "ambiguous": dict(sorted(self.ambiguous.items())), "ambiguous_total": sum(self.ambiguous.values()),
                "initials_restored": self.initials_restored, "initials_unresolved": self.initials_unresolved,
                "one_way_tokens": self.one_way_tokens,
                "unmatched_surrogate_like": self.unmatched_surrogate_like}


INITIAL_BEFORE = re.compile(r"(?<![A-Za-z])([A-Z])(\.?)(\s+)$")


def reidentify(text: str, idx: Index) -> tuple[str, Report]:
    rep = Report()
    rep.one_way_tokens = len(TOKEN_RE.findall(text))
    if idx.pattern is None:
        rep.unmatched_surrogate_like = _unmatched(text, idx, [])
        return text, rep
    out: list[str] = []
    pos = 0
    spans: list[tuple[int, int]] = []
    for m in idx.pattern.finditer(text):
        real, typ, status = _resolve(idx, m.group(1))
        if real is None:
            rep.ambiguous[typ] += 1
            continue
        chunk = text[pos:m.start()]
        if norm(m.group(1)) in idx.surnames:
            im = INITIAL_BEFORE.search(chunk)
            if im:
                cands = idx.initials.get(norm(m.group(1)), {}).get(im.group(1), set())
                if len(cands) == 1:
                    chunk = chunk[:im.start(1)] + next(iter(cands)) + chunk[im.end(1):]
                    rep.initials_restored += 1
                else:
                    rep.initials_unresolved += 1
        out.append(chunk)
        out.append(real)
        rep.restored[typ] += 1
        spans.append((m.start(), m.end()))
        pos = m.end()
    out.append(text[pos:])
    rep.unmatched_surrogate_like = _unmatched(text, idx, spans)
    return "".join(out), rep


def _unmatched(text: str, idx: Index, spans: list[tuple[int, int]]) -> int:
    """Capitalised words that are surrogate-pool names but were not restored (paraphrase, nicknames)."""
    n = 0
    for m in re.finditer(r"(?<![A-Za-z])[A-Z][a-z'\-]+(?![A-Za-z])", text):
        if norm(m.group(0)) in idx.pool_names and not any(a <= m.start() < b for a, b in spans):
            n += 1
    return n
