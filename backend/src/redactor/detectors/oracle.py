"""`gold_oracle`: the gold mentions as predictions (evaluation only).

Running the linker, policy and replacement on perfect detections measures them on their own (plan §4.1:
each stage can be varied as an experiment of its own). It reads the gold or silver annotation files, so it
is never used to produce exports and never scored as a detector.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..core.canonical import sha256_obj
from ..core.types import DocumentText, EntitySpan
from .base import register_detector


class GoldOracle:
    name = "gold_oracle"
    version = "0.1.0"

    def __init__(self, config: Mapping[str, Any] | None = None):
        self._hashes: dict[str, str] = {}

    def fingerprint(self) -> str:
        return f"{self.name}@{self.version}#{sha256_obj(self._hashes)[:12]}"

    def detect(self, doc: DocumentText) -> list[EntitySpan]:
        from ..core.canonical import sha256_file
        from ..evaluation.gold import load_gold, project_document
        from ..taxonomy import load_policy, load_taxonomy
        gold = load_gold(doc.document_id)
        if gold is None:
            return []
        self._hashes[doc.document_id] = sha256_file(gold.path)[:16]
        out = []
        for m in project_document(gold, doc, policy=load_policy(), taxonomy=load_taxonomy()):
            if not m.projected or m.excluded in ("region_only",):
                continue
            text = doc.pages[m.page].text if m.page is not None else doc.fields.get(m.field or "", "")
            attrs = dict(m.attributes)
            if m.role:
                attrs["role"] = m.role
            out.append(EntitySpan(doc.document_id, m.page, m.field, m.text_source_id, m.start, m.end, text[m.start:m.end],
                                  m.entity_type, m.entity_type, 1.0, f"{self.name}@{self.version}", m.mention_id, (),
                                  tuple(sorted((str(k), str(v)) for k, v in attrs.items()))))
        return out


@register_detector("gold_oracle")
def _factory(config: Mapping[str, Any]) -> GoldOracle:
    return GoldOracle(config)
