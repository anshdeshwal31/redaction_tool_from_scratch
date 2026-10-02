"""Layout-aware key–value rules for forms (plan §7.2): a label is matched to its value by position
on the page (right on the same line, or directly below), not by OCR reading order."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

import yaml
from rapidfuzz import fuzz

from ...core.canonical import sha256_obj
from ...core.types import BBox, EntitySpan, PageExtraction, Word
from ...paths import config_dir
from . import validators as idv
from .layout import Cell, Line, layout
from .patterns import DATE_RULES, EMAIL, PHONE_RULES

_NORM = re.compile(r"[^a-z0-9 ]+")
_OCR_FIX = str.maketrans({"0": "o", "1": "l", "5": "s", "|": "l"})


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", _NORM.sub(" ", text.lower().translate(_OCR_FIX))).strip()


@lru_cache(maxsize=4)
def load_form(name: str | None = None) -> dict[str, Any]:
    p = config_dir() / "forms" / f"{name or 'au_generic'}.yaml"
    return yaml.safe_load(Path(p).read_text(encoding="utf-8"))


class KVRules:
    def __init__(self, form: Mapping[str, Any]):
        self.form = dict(form)
        self.tol = form["tolerances"]
        self.labels: list[tuple[str, str | None, dict]] = []
        for entry in form["labels"]:
            for label in entry["label"]:
                self.labels.append((norm(label), entry.get("type"), dict(entry.get("attrs") or {})))
        self.labels.sort(key=lambda l: (-len(l[0]), l[0]))

    def match_label(self, words: list[Word]) -> tuple[int, str | None, dict, str] | None:
        """Longest label that the first k words of a cell spell; returns (k, type, attrs, label)."""
        for k in range(min(5, len(words)), 0, -1):
            text = norm(" ".join(w.text for w in words[:k]))
            if not text:
                continue
            for label, etype, attrs in self.labels:
                if text == label or (len(label) >= 6 and fuzz.ratio(text, label) >= 88):
                    return k, etype, attrs, label
        return None

    def _is_label_cell(self, cell: Cell) -> bool:
        return self.match_label(cell.words) is not None

    def _valid(self, etype: str, text: str) -> bool:
        if etype in ("DATE", "DATE_OF_BIRTH"):
            return any(rx.search(text) for _, rx in DATE_RULES)
        if etype == "PHONE":
            return any(rx.search(text) for _, _, rx in PHONE_RULES) or 8 <= len(idv.digits_only(text)) <= 12
        if etype == "EMAIL":
            return bool(EMAIL.search(text))
        if etype == "MEDICARE":
            return idv.medicare(text)
        if etype == "TFN":
            return idv.tfn(text)
        if etype == "ABN":
            return idv.abn(text)
        if etype == "ACN":
            return idv.acn(text)
        return bool(text.strip()) and any(ch.isalnum() for ch in text)

    def value_words(self, lines: list[Line], li: int, ci: int, k: int) -> list[Word]:
        line = lines[li]
        cell = line.cells[ci]
        rest = cell.words[k:]
        if rest:
            words = list(rest)
            for nxt in line.cells[ci + 1:]:
                if self._is_label_cell(nxt) or nxt.bbox.x0 - cell.bbox.x1 > self.tol["same_line_max_gap_pt"]:
                    break
                words += nxt.words
            return words[: self.tol["max_value_words"]]
        if ci + 1 < len(line.cells):
            nxt = line.cells[ci + 1]
            if not self._is_label_cell(nxt) and nxt.bbox.x0 - cell.bbox.x1 <= self.tol["same_line_max_gap_pt"]:
                return nxt.words[: self.tol["max_value_words"]]
        if li + 1 < len(lines):
            below = lines[li + 1]
            if 0 <= below.bbox.y0 - line.bbox.y1 <= self.tol["below_max_gap_pt"]:
                for c in below.cells:
                    if abs(c.bbox.x0 - cell.bbox.x0) <= self.tol["below_left_tolerance_pt"] and not self._is_label_cell(c):
                        return c.words[: self.tol["max_value_words"]]
        return []

    def spans(self, document_id: str, pe: PageExtraction, detector_tag: str) -> list[EntitySpan]:
        lines = layout(pe.words, float(self.tol["cell_gap_factor"]))
        out: list[EntitySpan] = []
        form = self.form["form"]
        for li, line in enumerate(lines):
            for ci, cell in enumerate(line.cells):
                m = self.match_label(cell.words)
                if m is None:
                    continue
                k, etype, attrs, label = m
                if etype is None:
                    continue
                words = self.value_words(lines, li, ci, k)
                words = [w for w in words if w.text not in (":", "-", "–")]
                if not words:
                    continue
                value_text = " ".join(w.text for w in words)
                if not self._valid(etype, value_text):
                    continue
                typed = etype not in ("PERSON", "ADDRESS", "ORGANIZATION")
                runs: list[list[Word]] = []
                for w in sorted(words, key=lambda w: w.index):
                    if runs and w.index == runs[-1][-1].index + 1:
                        runs[-1].append(w)
                    else:
                        runs.append([w])
                group = f"kv:{sha256_obj([document_id, pe.page, [w.index for w in words]])[:10]}" if len(runs) > 1 else None
                attr_items = tuple(sorted((str(a), str(b)) for a, b in attrs.items()))
                for run in runs:
                    s, e = run[0].start, run[-1].end
                    boxes = (BBox.union_all(w.bbox for w in run),)
                    out.append(EntitySpan(document_id, pe.page, None, pe.text_source_id, s, e, pe.text[s:e], etype, etype,
                                          0.85 if typed else 0.65, detector_tag, f"kv:{form}:{label.replace(' ', '_')}",
                                          boxes, attr_items, group))
        return out
