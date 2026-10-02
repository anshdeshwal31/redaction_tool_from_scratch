"""ORGANIZATION by suffix plus a KEEP gazetteer (plan §7.1)."""

from __future__ import annotations

import re
from typing import Mapping

from .resolve import TIER_CONTEXT, TIER_PATTERN, Cand

SUFFIX_ROLE = [
    (("Lawyers", "Solicitors", "Barristers", "Legal", "Chambers"), "law_firm"),
    (("Hospital", "Hospitals", "Private Hospital"), "hospital"),
    (("Clinic", "Medical Centre", "Medical Center", "Medical Practice", "Medical Group", "Physiotherapy", "Radiology",
      "Imaging", "Pathology", "Rehabilitation", "Psychology", "Psychiatry", "Orthopaedics", "Specialists",
      "Health Service", "Health Services"), "medical_practice"),
    (("Insurance", "Insurers"), "insurer"),
    (("University", "College", "School"), "education"),
    (("Council",), "government"),
]


class OrgRules:
    def __init__(self, cfg: Mapping):
        suffixes = sorted(cfg["org_suffixes"], key=len, reverse=True)
        suf = "|".join(re.escape(s).replace(r"\ ", r"\s+") for s in suffixes)
        word = r"(?:[A-Z][\w&'’.-]*|&)"
        self.suffix_rx = re.compile(rf"\b(?P<org>(?:{word}[ \t]+(?:(?:of|and|the|for|&)[ \t]+)?){{1,6}}?(?:{suf}))(?![\w])")
        self.gazetteer: list[tuple[str, str, re.Pattern]] = []
        for role, names in cfg["keep_gazetteer"].items():
            for n in sorted(names, key=len, reverse=True):
                self.gazetteer.append((n, role, re.compile(rf"(?<![\w]){re.escape(n)}(?![\w])", re.I)))
        self.gazetteer.sort(key=lambda g: (-len(g[0]), g[0]))

    @staticmethod
    def role_for(org: str) -> str | None:
        for suffixes, role in SUFFIX_ROLE:
            if any(org.endswith(s) for s in suffixes):
                return role
        return None

    def candidates(self, text: str) -> list[Cand]:
        out = []
        for name, role, rx in self.gazetteer:
            for m in rx.finditer(text):
                out.append(Cand(m.start(), m.end(), "ORGANIZATION", "orgs.gazetteer", TIER_CONTEXT, 0.95, (("role", role),)))
        for m in self.suffix_rx.finditer(text):
            s, e = m.start("org"), m.end("org")
            org = text[s:e]
            if "\n" in org:
                cut = org.rfind("\n") + 1
                s += cut
                org = org[cut:]
            if len(org.split()) < 2:
                continue
            role = self.role_for(org)
            attrs = (("role", role),) if role else ()
            out.append(Cand(s, e, "ORGANIZATION", "orgs.suffix", TIER_PATTERN, 0.75, attrs))
        return out
