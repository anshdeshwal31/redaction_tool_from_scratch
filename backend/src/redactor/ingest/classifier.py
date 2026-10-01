"""Page classifier (plan §3.3, §1.2 design consequences 1–3)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

import yaml

from ..core.canonical import sha256_obj
from ..dataset.models import Classification, Signals
from ..paths import config_dir
from .pdfinfo import PageStructure


@dataclass(frozen=True)
class ClassifierConfig:
    raw: Mapping[str, Any]

    @property
    def name(self) -> str:
        return self.raw["classifier"]

    @property
    def fingerprint(self) -> str:
        return f"{self.name}#{sha256_obj(self.raw)[:8]}"


@lru_cache(maxsize=4)
def load_config(path: str | None = None) -> ClassifierConfig:
    p = Path(path) if path else config_dir() / "extraction" / "page_classifier.yaml"
    return ClassifierConfig(yaml.safe_load(p.read_text(encoding="utf-8")))


def signals_of(ps: PageStructure) -> Signals:
    page_area = ps.width_pt * ps.height_pt if ps.width_pt and ps.height_pt else 1.0
    largest = max((r.bbox.area / page_area for r in ps.image_regions), default=0.0)
    return Signals(
        visible_text_chars=ps.visible_text_chars,
        invisible_text_chars=ps.invisible_text_chars,
        image_count=len(ps.image_regions),
        image_coverage=round(ps.image_coverage, 4),
        image_dpi=sorted({r.dpi for r in ps.image_regions if r.dpi}),
        vector_paths=ps.vector_paths,
        small_filled_paths=ps.small_filled_paths,
        alnum_ratio=None if ps.alnum_ratio is None else round(ps.alnum_ratio, 4),
        bad_char_ratio=None if ps.bad_char_ratio is None else round(ps.bad_char_ratio, 4),
        largest_image_fraction=round(min(1.0, largest), 4),
    )


def classify(signals: Signals, cfg: ClassifierConfig | None = None) -> Classification:
    cfg = cfg or load_config()
    c = cfg.raw
    ut = c["usable_text"]
    visible = signals.visible_text_chars
    if (visible >= ut["min_visible_chars"] and (signals.alnum_ratio or 0) >= ut["min_alnum_ratio"]
            and (signals.bad_char_ratio or 0) <= ut["max_bad_char_ratio"]):
        text_layer = "usable"
    elif visible > 0:
        text_layer = "present_unusable"
    else:
        text_layer = "absent"
    invisible_layer = signals.invisible_text_chars > 0

    if text_layer == "usable":
        if signals.largest_image_fraction >= c["hybrid"]["min_raster_fraction"]:
            kind, scope = "hybrid", "raster_regions"
        elif signals.largest_image_fraction >= c["raster_region_min_fraction"]:
            kind, scope = "born_digital", "raster_regions"
        else:
            kind, scope = "born_digital", "none"
    elif signals.image_coverage >= c["scanned"]["min_image_coverage"]:
        kind, scope = "scanned", "full_page"
    elif (signals.small_filled_paths >= c["vector_outlined"]["min_small_filled_paths"]
          and signals.image_coverage < c["vector_outlined"]["max_image_coverage"]):
        kind, scope = "vector_outlined", "full_page"
    elif (visible == 0 and signals.invisible_text_chars == 0 and signals.vector_paths <= c["blank"]["max_paths"]
          and signals.image_coverage <= c["blank"]["max_image_coverage"]):
        kind, scope = "blank", "none"
    else:
        kind, scope = "unknown", "full_page"  # always to human review (§3.3)
    return Classification(content_kind=kind, text_layer=text_layer, invisible_ocr_layer=invisible_layer,
                          ocr_required=scope != "none", ocr_scope=scope, classifier=cfg.fingerprint,
                          human_verified=False)
