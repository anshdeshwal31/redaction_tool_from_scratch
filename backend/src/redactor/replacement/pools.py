"""Versioned, hashed surrogate pools (config/surrogates/*.yaml). Entries that occur in the matter's
extracted text (case-insensitive, whole words) are removed, so a surrogate never collides with a real
name the detector missed and re-identification stays unambiguous (plan §4.9.1)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Iterable

import yaml

from ..core.canonical import sha256_file
from ..paths import config_dir

FILES = ("names.v0.1.yaml", "places.v0.1.yaml", "contacts.v0.1.yaml")
LEXICON = "first_name_gender.v0.1.yaml"


@lru_cache(maxsize=4)
def _raw() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for f in FILES:
        out.update(yaml.safe_load((config_dir() / "surrogates" / f).read_text(encoding="utf-8")))
    return out


def pool_hashes() -> dict[str, str]:
    return {f: sha256_file(config_dir() / "surrogates" / f) for f in FILES + (LEXICON,)}


@lru_cache(maxsize=1)
def _lexicon() -> dict[str, str]:
    raw = yaml.safe_load((config_dir() / "surrogates" / LEXICON).read_text(encoding="utf-8"))
    out = {n.casefold(): "female" for n in raw["female"]}
    for n in raw["male"]:
        out[n.casefold()] = "unknown" if out.get(n.casefold()) == "female" else "male"
    return out


@dataclass
class Pools:
    surnames: list[str]
    female: list[str]
    male: list[str]
    unisex: list[str]
    street_names: list[str]
    street_types: list[str]
    states: dict[str, list[tuple[str, str]]]
    org_stems: list[str]
    org_suffixes: list[str]
    mobile: list[str]
    landline_blocks: list[str]
    area_codes: list[str]
    freephone: list[str]
    local_rate: list[str]
    email_domains: list[str]
    url_domain: str
    honorific_gender: dict[str, str]
    state_aliases: dict[str, str]
    postcode_state: dict[str, str]
    default_state: str
    removed: int = 0
    first_name_gender: dict[str, str] = field(default_factory=dict)

    def first_names(self, gender: str) -> list[str]:
        return {"female": self.female, "male": self.male}.get(gender, self.unisex)


def _words(texts: Iterable[str]) -> set[str]:
    out: set[str] = set()
    for t in texts:
        out.update(w.casefold() for w in re.findall(r"[A-Za-z][A-Za-z'\-]+", t))
    return out


def load_pools(matter_texts: Iterable[str] = ()) -> Pools:
    raw = _raw()
    texts = list(matter_texts)
    words = _words(texts)
    joined = " ".join(t.casefold() for t in texts)
    removed = 0

    def keep(items: list[str]) -> list[str]:
        nonlocal removed
        out = []
        for it in items:
            toks = [w.casefold() for w in re.findall(r"[A-Za-z][A-Za-z'\-]+", it)]
            if toks and (all(t in words for t in toks) or it.casefold() in joined):
                removed += 1
                continue
            out.append(it)
        return out

    states = {}
    for st, rows in raw["states"].items():
        rows2 = [(s, p) for s, p in rows if s.casefold() not in joined]
        removed += len(rows) - len(rows2)
        states[st] = rows2 or [tuple(r) for r in rows]  # never leave a state empty
    fng = _lexicon()   # first-name lexicon for gender inference (§4.9.1, source 3)
    return Pools(surnames=keep(raw["surnames"]), female=keep(raw["female"]), male=keep(raw["male"]), unisex=keep(raw["unisex"]),
                 street_names=keep(raw["street_names"]), street_types=list(raw["street_types"]), states=states,
                 org_stems=keep(raw["org_stems"]), org_suffixes=list(raw["org_suffixes"]), mobile=list(raw["mobile"]),
                 landline_blocks=list(raw["landline_blocks"]), area_codes=list(raw["area_codes"]),
                 freephone=list(raw["freephone"]), local_rate=list(raw["local_rate"]), email_domains=list(raw["email_domains"]),
                 url_domain=raw["url_domain"], honorific_gender=dict(raw["honorific_gender"]),
                 state_aliases=dict(raw["state_aliases"]), postcode_state=dict(raw["postcode_state"]),
                 default_state=raw["default_state"], removed=removed, first_name_gender=fng)
