"""Word boxes to lines and cells (plan §7.2). Deterministic: rounded coordinates, stable sorts."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Sequence

from ...core.types import BBox, Word


@dataclass
class Cell:
    words: list[Word]

    @property
    def bbox(self) -> BBox:
        return BBox.union_all(w.bbox for w in self.words)

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


@dataclass
class Line:
    words: list[Word]
    cells: list[Cell]

    @property
    def bbox(self) -> BBox:
        return BBox.union_all(w.bbox for w in self.words)


def group_lines(words: Sequence[Word]) -> list[list[Word]]:
    ordered = sorted(words, key=lambda w: (round(w.bbox.center[1], 1), round(w.bbox.x0, 1), w.index))
    lines: list[list[Word]] = []
    for w in ordered:
        placed = False
        for line in reversed(lines[-3:]):
            ref = line[-1].bbox
            overlap = min(ref.y1, w.bbox.y1) - max(ref.y0, w.bbox.y0)
            if overlap >= 0.5 * min(ref.height, w.bbox.height) and w.bbox.height > 0:
                line.append(w)
                placed = True
                break
        if not placed:
            lines.append([w])
    for line in lines:
        line.sort(key=lambda w: (round(w.bbox.x0, 1), w.index))
    lines.sort(key=lambda l: (round(min(w.bbox.y0 for w in l), 1), round(l[0].bbox.x0, 1)))
    return lines


def median_char_width(words: Sequence[Word]) -> float:
    widths = [w.bbox.width / max(1, len(w.text)) for w in words if w.text]
    return median(widths) if widths else 5.0


def split_cells(line: list[Word], gap: float) -> list[Cell]:
    cells: list[Cell] = []
    cur: list[Word] = []
    for w in line:
        if cur and w.bbox.x0 - cur[-1].bbox.x1 > gap:
            cells.append(Cell(cur))
            cur = []
        cur.append(w)
    if cur:
        cells.append(Cell(cur))
    return cells


def layout(words: Sequence[Word], gap_factor: float) -> list[Line]:
    if not words:
        return []
    gap = gap_factor * median_char_width(words)
    return [Line(l, split_cells(l, gap)) for l in group_lines(words)]
