"""Baseline detector v0 (`baseline@0.1.0`) and v1 (`baseline@0.2.0`, + layout key–value rules).

Deterministic: regexes, validators, lexicons, fixed tie-breaks; no models, no network (plan §7).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from ...core.canonical import sha256_hex, sha256_obj
from ...core.types import DocumentText, EntitySpan, PageExtraction
from ...paths import config_dir
from ..base import register_detector
from . import context as ctx
from . import patterns
from .lexicons import LexiconRules
from .names import NameRules, NameSeeds
from .orgs import OrgRules
from .resolve import TIER_CONTEXT, Cand, resolve

_SOURCE_FILES = ("patterns.py", "context.py", "names.py", "orgs.py", "lexicons.py", "resolve.py", "detector.py",
                 "validators.py", "layout.py", "kv_rules.py")


@lru_cache(maxsize=4)
def load_config(path: str | None = None) -> dict[str, Any]:
    p = Path(path) if path else config_dir() / "baseline.v0.1.yaml"
    cfg = yaml.safe_load(p.read_text(encoding="utf-8"))
    for key, value in cfg.items():  # YAML 1.1 turns Yes/No/On into booleans; lists must stay strings
        if isinstance(value, list) and any(not isinstance(v, str) for v in value):
            raise ValueError(f"baseline config list '{key}' has a non-string item (quote it)")
    return cfg


def source_sha256() -> str:
    here = Path(__file__).parent
    return sha256_hex(b"".join((here / f).read_bytes() for f in _SOURCE_FILES if (here / f).exists()))


class BaselineDetector:
    name = "baseline"
    version = "0.1.0"
    layout_rules = False

    def __init__(self, config: Mapping[str, Any] | None = None):
        config = dict(config or {})
        self.cfg = load_config(config.get("path"))
        self.enabled = set(self.cfg["enabled"])
        self.window = int(self.cfg.get("context_window_chars", 48))
        self.names = NameRules(self.cfg)
        self.orgs = OrgRules(self.cfg)
        self.lex = LexiconRules(self.cfg)
        self.seeds = NameSeeds()
        self._prepared_for: tuple[str, ...] | None = None

    def fingerprint(self) -> str:
        return f"{self.name}@{self.version}#{sha256_obj({'cfg': self.cfg, 'src': source_sha256(), 'layout': self.layout_rules})[:12]}"

    @property
    def tag(self) -> str:
        return f"{self.name}@{self.version}"

    # --- matter-level first pass (plan §4.1): collect name seeds across every document
    def prepare(self, docs: Sequence[DocumentText]) -> None:
        texts = []
        for d in sorted(docs, key=lambda d: d.document_id):
            texts += [d.pages[p].text for p in d.page_numbers()]
            texts += [d.fields[f] for f in sorted(d.fields)]
        self.seeds = self.names.collect(texts) if "names" in self.enabled else NameSeeds()
        self._prepared_for = tuple(sorted(d.document_id for d in docs))

    def _text_cands(self, text: str) -> list[Cand]:
        cands: list[Cand] = []
        dates = patterns.date_candidates(text) if "patterns" in self.enabled else []
        if "patterns" in self.enabled:
            cands += dates + patterns.phone_candidates(text) + patterns.simple_candidates(text)
        if "checksum_identifiers" in self.enabled:
            cands += ctx.checksum_candidates(text, self.window)
        if "context_identifiers" in self.enabled:
            cands += ctx.context_candidates(text, self.window)
        if "dob" in self.enabled:
            cands += ctx.dob_candidates(text, dates, self.window)
        if "names" in self.enabled:
            cands += self.names.seeds(text) + self.names.propagate(text, self.seeds)
        if "orgs" in self.enabled:
            cands += self.orgs.candidates(text)
        if "lexicons" in self.enabled:
            cands += self.lex.candidates(text)
        kept = resolve(cands)
        if "date_role" in self.enabled:
            kept = [c.with_attrs(date_role=ctx.date_role_for(text, c.start, self.cfg["date_roles"]))
                    if c.etype == "DATE" else c for c in kept]
        return kept

    def _to_span(self, doc: DocumentText, c: Cand, text: str, page: int | None, field: str | None,
                 source: str, pe: PageExtraction | None) -> EntitySpan:
        boxes = ()
        if pe is not None:
            from ...dataset.silver import regions_for_span
            boxes = tuple(regions_for_span(pe, c.start, c.end))
        return EntitySpan(doc.document_id, page, field, source, c.start, c.end, text[c.start:c.end], c.etype, c.etype,
                          round(c.confidence, 4), self.tag, c.rule_id, boxes, c.attrs)

    def page_extra(self, doc: DocumentText, page: int, pe: PageExtraction, kept: list[Cand]) -> list[EntitySpan]:
        return []

    def detect(self, doc: DocumentText) -> list[EntitySpan]:
        if self._prepared_for is None:
            self.prepare([doc])
        out: list[EntitySpan] = []
        for page in doc.page_numbers():
            pe = doc.pages[page]
            kept = self._text_cands(pe.text)
            # Layout (v1) spans carry positional context: they replace overlapping pattern/lexicon-tier
            # spans, never checksum-validated or keyword-context ones.
            extras: list[EntitySpan] = []
            for e in sorted(self.page_extra(doc, page, pe, kept), key=EntitySpan.sort_key):
                overlapping = [c for c in kept if c.start < e.end and e.start < c.end]
                if any(c.tier >= TIER_CONTEXT for c in overlapping):
                    continue
                if any(e.start < x.end and x.start < e.end for x in extras):
                    continue
                kept = [c for c in kept if c not in overlapping]
                extras.append(e)
            spans = [self._to_span(doc, c, pe.text, page, None, pe.text_source_id, pe) for c in kept]
            out += spans + extras
        for field in sorted(doc.fields):
            text = doc.fields[field]
            for c in self._text_cands(text):
                out.append(self._to_span(doc, c, text, None, field, doc.field_source_id(field), None))
        return sorted(out, key=EntitySpan.sort_key)


class BaselineV1Detector(BaselineDetector):
    """v0 plus layout-aware key–value rules for forms (plan §7.2)."""
    version = "0.2.0"
    layout_rules = True

    def __init__(self, config: Mapping[str, Any] | None = None):
        super().__init__(config)
        from .kv_rules import KVRules, load_form
        self.kv = KVRules(load_form((config or {}).get("form")))

    def fingerprint(self) -> str:
        return f"{super().fingerprint()}+{sha256_obj(self.kv.form)[:8]}"

    def page_extra(self, doc: DocumentText, page: int, pe: PageExtraction, kept: list[Cand]) -> list[EntitySpan]:
        return self.kv.spans(doc.document_id, pe, self.tag)


@register_detector("baseline")
def _make_v0(config: Mapping[str, Any]) -> BaselineDetector:
    return BaselineDetector(config)


@register_detector("baseline_v1")
def _make_v1(config: Mapping[str, Any]) -> BaselineV1Detector:
    return BaselineV1Detector(config)
