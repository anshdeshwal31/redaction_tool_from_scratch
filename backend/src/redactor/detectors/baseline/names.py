"""PERSON detection (plan §7.1): seeds from honorifics, form labels and signature blocks, then
whole-word propagation of seeded names and their components across the whole matter."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from .resolve import TIER_CONTEXT, TIER_LEXICON, TIER_PATTERN, Cand

TOKEN = r"(?:(?:Mc|Mac|O['’]|D['’])?[A-Z][a-z]+(?:[-'’][A-Z]?[a-z]+)*|[A-Z]{2,}(?:[-'’][A-Z]+)*|[A-Z]\.?)"
POSSESSIVE = re.compile(r"['’]s$")


@dataclass
class NameSeeds:
    full: set[str] = field(default_factory=set)
    surnames: set[str] = field(default_factory=set)
    given: set[str] = field(default_factory=set)


class NameRules:
    def __init__(self, cfg: Mapping):
        self.stop = {w.lower() for w in cfg["name_stopwords"]}
        hon = "|".join(sorted((re.escape(h) for h in cfg["honorifics"]), key=len, reverse=True))
        self.honorific_rx = re.compile(rf"\b(?:{hon})\.?[ \t]+(?P<name>{TOKEN}(?:[ \t]+{TOKEN}){{0,2}})")
        labels = "|".join(sorted((re.escape(l) for l in cfg["name_labels"]), key=len, reverse=True))
        self.label_rx = re.compile(rf"(?im)(?:^|\b)(?:{labels})\s*[:\-–]\s*(?:(?:{hon})\.?\s+)?(?P<name>{TOKEN}(?:,?[ \t]+{TOKEN}){{0,3}})")
        self.re_rx = re.compile(rf"\bRe\s*:\s*(?:(?:{hon})\.?\s+)?(?P<name>{TOKEN}(?:[ \t]+{TOKEN}){{1,3}})")
        openers = "|".join(re.escape(o) for o in cfg["signature_openers"])
        self.sign_rx = re.compile(rf"(?i)\b(?:{openers})\b[,.]?")
        self.sig_line_rx = re.compile(rf"^\s*(?:(?:{hon})\.?\s+)?(?P<name>{TOKEN}(?:[ \t]+{TOKEN}){{1,3}})\s*(?:,.*)?$")
        self.given_min = int(cfg.get("given_name_min_len", 2))
        self.surname_min = int(cfg.get("surname_min_len", 3))

    def _clean(self, raw: str, start: int) -> tuple[int, int, list[str]] | None:
        text = POSSESSIVE.sub("", raw.rstrip(" ,\n\t"))
        tokens = re.split(r"[\s,]+", text.strip())
        while tokens and tokens[-1].lower().rstrip(".") in self.stop:
            last = tokens.pop()
            text = text[: text.rfind(last)].rstrip(" ,")
        while tokens and tokens[0].lower().rstrip(".") in self.stop:
            first = tokens.pop(0)
            cut = text.find(first) + len(first)
            start += len(text[:cut]) + (len(text[cut:]) - len(text[cut:].lstrip(" ,")))
            text = text[cut:].lstrip(" ,")
        if not tokens or not any(len(t.rstrip(".")) >= 2 for t in tokens):
            return None
        return start, start + len(text), tokens

    def seeds(self, text: str) -> list[Cand]:
        out: list[Cand] = []
        for rx, rule, tier in ((self.honorific_rx, "names.honorific", TIER_PATTERN),
                               (self.label_rx, "names.label", TIER_CONTEXT), (self.re_rx, "names.re", TIER_CONTEXT)):
            for m in rx.finditer(text):
                cleaned = self._clean(m.group("name"), m.start("name"))
                if cleaned:
                    s, e, _ = cleaned
                    out.append(Cand(s, e, "PERSON", rule, tier, 0.85))
        for m in self.sign_rx.finditer(text):
            rest = text[m.end():].split("\n")
            offset = m.end()
            for i, line in enumerate(rest[:4]):
                if i > 0 and line.strip():
                    sm = self.sig_line_rx.match(line)
                    if sm:
                        cleaned = self._clean(sm.group("name"), offset + sm.start("name"))
                        if cleaned:
                            s, e, _ = cleaned
                            out.append(Cand(s, e, "PERSON", "names.signature", TIER_PATTERN, 0.8))
                    break
                offset += len(line) + 1
        return out

    def collect(self, texts: Iterable[str]) -> NameSeeds:
        seeds = NameSeeds()
        for text in texts:
            for c in self.seeds(text):
                name = text[c.start:c.end]
                if "," in name:  # "SURNAME, Given"
                    sur, _, given = name.partition(",")
                    tokens = [given.strip().split()[0]] if given.strip() else []
                    sur = sur.strip()
                else:
                    tokens = name.split()
                    sur = tokens[-1] if len(tokens) >= 1 else ""
                seeds.full.add(name)
                sur = sur.rstrip(".")
                if len(sur) >= self.surname_min and sur.lower() not in self.stop:
                    seeds.surnames.add(sur)
                if len(tokens) >= 2 or "," in name:
                    g = tokens[0].rstrip(".")
                    if len(g) >= self.given_min and g.lower() not in self.stop and not re.fullmatch(r"[A-Z]\.?", tokens[0]):
                        seeds.given.add(g)
        return seeds

    def propagate(self, text: str, seeds: NameSeeds) -> list[Cand]:
        out = []
        terms = sorted(seeds.full | seeds.surnames | seeds.given, key=lambda t: (-len(t), t))
        for term in terms:
            variants = {term, term.upper()}
            for v in sorted(variants):
                for m in re.finditer(rf"(?<![\w'’-]){re.escape(v)}(?![\w-])", text):
                    tier = TIER_LEXICON
                    out.append(Cand(m.start(), m.end(), "PERSON", "names.propagated", tier, 0.6))
        return out


def join_texts(texts: Sequence[str]) -> list[str]:
    return list(texts)
