"""`audit_scans`: the non-detector scans the recall audit pools (plan §9.4 P7). Audit only; never scored
as a candidate system and never used for exports.

- checksum scan: digit runs that validate as TFN, ABN, ACN, Medicare, IHI or a provider number;
- NATIONALITY and GENDER lexicon scan (the baseline's closed lexicons);
- capitalised-token scan: Title-case or ALL-CAPS tokens of 3+ letters that are not sentence-initial and
  not on the baseline's stop-list (type OTHER); a reviewer accepts or rejects each one.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

import yaml

from ..core.canonical import sha256_obj
from ..core.types import DocumentText, EntitySpan
from ..paths import config_dir
from .base import register_detector

VALIDATOR_TYPES = {"tfn": "TFN", "abn": "ABN", "acn": "ACN", "medicare": "MEDICARE", "ihi": "IHI", "provider_number": "PROVIDER_NUMBER"}
DIGITS = re.compile(r"\d[\d \-]{6,}\d")
CAPS = re.compile(r"(?<![A-Za-z'\-])[A-Z][A-Za-z'\-]{2,}(?![A-Za-z])")


class AuditScans:
    name = "audit_scans"
    version = "0.1.0"

    def __init__(self, config: Mapping[str, Any] | None = None):
        from .baseline.lexicons import LexiconRules
        self.cfg = yaml.safe_load((config_dir() / "baseline.v0.1.yaml").read_text(encoding="utf-8"))
        self.lex = LexiconRules(self.cfg)
        self.stop = {w.lower() for w in self.cfg.get("name_stopwords", [])}

    def fingerprint(self) -> str:
        return f"{self.name}@{self.version}#{sha256_obj({'stop': sorted(self.stop), 'types': VALIDATOR_TYPES})[:12]}"

    def _scan(self, text: str) -> list[tuple[int, int, str, str]]:
        from .baseline import validators as v
        out: list[tuple[int, int, str, str]] = []
        for m in DIGITS.finditer(text):
            names = sorted(n for n in v.any_valid(m.group(0)) if n in VALIDATOR_TYPES)
            if names:
                out.append((m.start(), m.end(), VALIDATOR_TYPES[names[0]], f"scan.checksum.{names[0]}"))
        for c in self.lex.candidates(text):
            if c.etype in ("NATIONALITY", "GENDER"):
                out.append((c.start, c.end, c.etype, f"scan.{c.rule_id}"))
        for m in CAPS.finditer(text):
            before = text[:m.start()].rstrip(" \t")
            if not before or before[-1] in ".!?:\n" or m.group(0).lower() in self.stop:
                continue
            out.append((m.start(), m.end(), "OTHER", "scan.capitalised"))
        return sorted(set(out))

    def detect(self, doc: DocumentText) -> list[EntitySpan]:
        spans = []
        units = [(p, None, pe.text_source_id, pe.text) for p, pe in sorted(doc.pages.items())]
        units += [(None, f, "field", t) for f, t in sorted(doc.fields.items())]
        for page, field, src, text in units:
            for s, e, t, rule in self._scan(text):
                spans.append(EntitySpan(doc.document_id, page, field, src, s, e, text[s:e], t, t, None, f"{self.name}@{self.version}", rule))
        return sorted(spans, key=EntitySpan.sort_key)


@register_detector("audit_scans")
def _make(config: Mapping[str, Any]) -> AuditScans:
    return AuditScans(config)
