"""Person names, replaced component by component (plan §4.9.1).

Each surname maps to one synthetic surname; each (first name, gender) pair to one synthetic first name
starting with the per-matter permutation of its initial; initials go through that permutation. So
"John Smith", "John", "Mr Smith", "SMITH, John" and "J. Smith" stay mutually consistent even when
linking is imperfect. Titles stay (they are outside the span); casing and "Surname, Given" order stay.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .keys import letter_permutation, norm, pick
from .pools import Pools

HONORIFICS = ("Mr", "Mrs", "Ms", "Miss", "Mstr", "Master", "Mx", "Dr", "Prof", "Professor", "A/Prof", "Sir", "Dame", "Madam")
_TOKEN = re.compile(r"[A-Za-z][A-Za-z'\-]*\.?|[A-Za-z]")


@dataclass(frozen=True)
class NameToken:
    start: int
    end: int
    text: str
    role: str  # surname | given | initial


def honorific_before(text: str, start: int) -> str | None:
    before = text[max(0, start - 14):start].rstrip()
    m = re.search(r"(Mr|Mrs|Ms|Miss|Mstr|Master|Mx|Dr|Prof|Professor|A/Prof|Sir|Dame|Madam)\.?$", before)
    return m.group(1) if m else None


def _case(original: str, new: str) -> str:
    core = re.sub(r"[^A-Za-z]", "", original)
    if len(core) > 1 and core.isupper():
        return new.upper()
    if core.islower():
        return new.lower()
    return new[:1].upper() + new[1:]


def parse(surface: str, *, known_surnames: set[str] = frozenset(), known_given: set[str] = frozenset(),
          honorific: str | None = None, lexicon: dict[str, str] | None = None) -> list[NameToken]:
    toks = [(m.start(), m.end(), m.group(0)) for m in _TOKEN.finditer(surface)]
    toks = [t for t in toks if t[2].rstrip(".") not in HONORIFICS]
    if not toks:
        return []
    def is_init(t: str) -> bool:
        core = t.rstrip(".")
        return len(core) == 1 or (2 <= len(core) <= 3 and core.isalpha() and core.isupper() and len(toks) == 1)
    out: list[NameToken] = []
    comma = surface.find(",")
    if comma != -1:  # "SURNAME, Given Middle"
        for s, e, t in toks:
            role = "initial" if is_init(t) else ("surname" if s < comma else "given")
            out.append(NameToken(s, e, t, role))
        return out
    first = toks[0][2].rstrip(".")
    if len(toks) >= 2 and first.isupper() and len(first) > 1 and not all(t[2].isupper() for t in toks):
        # "KNIGHT Julie Michelle": surname first in capitals
        return [NameToken(s, e, t, "initial" if is_init(t) else ("surname" if i == 0 else "given")) for i, (s, e, t) in enumerate(toks)]
    if len(toks) == 1:
        s, e, t = toks[0]
        if is_init(t):
            return [NameToken(s, e, t, "initial")]
        n = norm(t)
        if n in known_surnames and n not in known_given:
            role = "surname"
        elif n in known_given and n not in known_surnames:
            role = "given"
        elif honorific and honorific not in ("Master", "Mstr"):
            role = "surname"
        elif lexicon and n in lexicon:
            role = "given"
        else:
            role = "surname"
        return [NameToken(s, e, t, role)]
    out = []
    for i, (s, e, t) in enumerate(toks):
        role = "initial" if is_init(t) else ("surname" if i == len(toks) - 1 else "given")
        out.append(NameToken(s, e, t, role))
    return out


NAME_KINDS = ("surname", "given:female", "given:male", "given:unknown", "org")


class NameMapper:
    def __init__(self, key: bytes, pools: Pools, vault):
        self.key = key
        self.pools = pools
        self.vault = vault
        self.perm = letter_permutation(key)

    def _name_taken(self) -> set[str]:
        """Surrogates already issued as any name-like value (surname, given name of any gender, organisation
        stem): a surrogate is issued once across them, so the reverse mapping stays injective (§4.12.4)."""
        return set().union(*(self.vault.issued(k) for k in NAME_KINDS))

    def surname(self, real: str) -> str:
        n = norm(real)
        got = self.vault.get("surname", n)
        if got is None:
            taken = self._name_taken()
            pool = [x for x in self.pools.surnames if x not in taken] or _combos(self.pools.surnames, taken)
            got, _ = pick(self.key, pool, "SURNAME", n)
            self.vault.put("surname", n, got)
        return got

    def given(self, real: str, gender: str) -> str:
        n = norm(real)
        kind = f"given:{gender}"
        got = self.vault.get(kind, n)
        if got is None:
            pool = self.pools.first_names(gender)
            initial = self.perm.get(real[:1].upper(), real[:1].upper())
            preferred = [x for x in pool if x[:1].upper() == initial]
            taken = self._name_taken()
            cands = [x for x in preferred if x not in taken] or [x for x in pool if x not in taken] or _combos(pool, taken, initial)
            got, _ = pick(self.key, sorted(cands), "GIVEN", gender, n)
            self.vault.put(kind, n, got)
        return got

    def initial(self, real: str) -> str:
        """Each letter through the per-matter permutation ("J." -> "Q.", "JK" -> "QD")."""
        out = []
        for ch in real:
            if ch.isalpha():
                m = self.perm.get(ch.upper(), ch.upper())
                out.append(m if ch.isupper() else m.lower())
            else:
                out.append(ch)
        return "".join(out)

    def replace(self, surface: str, tokens: list[NameToken], gender: str) -> tuple[str, dict[str, str]]:
        """Returns the replacement text and the identity components (for consistency metrics)."""
        out = surface
        ident: dict[str, str] = {}
        for t in sorted(tokens, key=lambda x: -x.start):
            core = t.text.rstrip(".")
            if t.role == "initial":
                new = self.initial(t.text)
            elif t.role == "surname":
                new = _case(core, self.surname(core))
                ident["surname"] = new.casefold()
            else:
                new = _case(core, self.given(core, gender))
                ident["given"] = new.casefold()     # processed right to left: the first given name wins
            if t.role != "initial" and t.text.endswith(".") and not new.endswith("."):
                new += "."
            out = out[:t.start] + new + out[t.end:]
        return out, ident


def _combos(pool: list[str], taken: set[str], initial: str | None = None) -> list[str]:
    """Hyphenated pairs when a pool is used up (very large matters); the pair keeps the pool's gender."""
    out = [f"{a}-{b}" for a in pool for b in pool if a != b and f"{a}-{b}" not in taken]
    pref = [x for x in out if initial and x[:1].upper() == initial]
    return pref or out


def gender_of(given_tokens: list[str], honorifics: list[str], pools: Pools, registry_gender: str | None = None) -> str:
    """Honorific first, then registry/linker evidence, then the first-name lexicon, else unknown (§4.9.1)."""
    hg = [pools.honorific_gender.get(h) for h in honorifics if pools.honorific_gender.get(h) in ("female", "male")]
    if hg:
        return max(sorted(set(hg)), key=hg.count)
    if registry_gender in ("female", "male"):
        return registry_gender
    for g in given_tokens:
        lex = pools.first_name_gender.get(norm(g))
        if lex in ("female", "male"):
            return lex
    return "unknown"
