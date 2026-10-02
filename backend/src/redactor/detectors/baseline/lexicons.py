"""Closed lexicons (plan §7.1): GENDER, NATIONALITY (with proper-noun exclusions), LOCATION (states)."""

from __future__ import annotations

import re
from typing import Mapping

from .resolve import TIER_LEXICON, TIER_PATTERN, Cand


class LexiconRules:
    def __init__(self, cfg: Mapping):
        g = "|".join(sorted((re.escape(w) for w in cfg["gender_words"]), key=len, reverse=True))
        self.gender_rx = re.compile(rf"(?<![\w-])(?:{g})(?![\w-])", re.I)
        self.sex_rx = re.compile(r"\bSex\s*[:\-]\s*(?P<v>M|F|Male|Female)\b", re.I)
        dem = "|".join(sorted((re.escape(d) for d in cfg["demonyms"]), key=len, reverse=True))
        # a demonym is a nationality unless it starts a proper noun ("Australian Taxation Office")
        self.demonym_rx = re.compile(rf"(?<![\w-])(?P<d>{dem})(?![\w-])(?!\s+(?!(?:national|citizen|citizens|nationals|born|descent|origin)\b)[A-Z][a-z])")
        countries = "|".join(sorted((re.escape(c) for c in cfg["countries"]), key=len, reverse=True))
        cues = "|".join(sorted((re.escape(c) for c in cfg["nationality_cues"]), key=len, reverse=True))
        self.cue_rx = re.compile(rf"\b(?:{cues})\s+(?:the\s+)?(?P<c>{countries})\b", re.I)
        self.citizen_rx = re.compile(rf"\b(?P<d>{dem})\s+(?:citizen|national|citizenship)\b", re.I)
        states = cfg["states"]
        names = "|".join(sorted((re.escape(n) for n in states), key=len, reverse=True))
        abbrs = "|".join(sorted(states.values(), key=len, reverse=True))
        self.state_rx = re.compile(rf"(?<![\w-])(?:{names}|(?:{abbrs}))(?![\w-])")

    def candidates(self, text: str) -> list[Cand]:
        out = []
        for m in self.gender_rx.finditer(text):
            out.append(Cand(m.start(), m.end(), "GENDER", "lex.gender", TIER_LEXICON, 0.7))
        for m in self.sex_rx.finditer(text):
            out.append(Cand(m.start("v"), m.end("v"), "GENDER", "lex.sex_label", TIER_PATTERN, 0.9))
        for m in self.citizen_rx.finditer(text):
            out.append(Cand(m.start("d"), m.end("d"), "NATIONALITY", "lex.citizenship", TIER_PATTERN, 0.85, (("form", "citizenship"),)))
        for m in self.demonym_rx.finditer(text):
            out.append(Cand(m.start("d"), m.end("d"), "NATIONALITY", "lex.demonym", TIER_LEXICON, 0.6, (("form", "demonym"),)))
        for m in self.cue_rx.finditer(text):
            out.append(Cand(m.start("c"), m.end("c"), "NATIONALITY", "lex.country_of_origin", TIER_PATTERN, 0.8,
                            (("form", "country_of_origin"),)))
        for m in self.state_rx.finditer(text):
            out.append(Cand(m.start(), m.end(), "LOCATION", "lex.state", TIER_LEXICON, 0.6, (("granularity", "state"),)))
        return out
