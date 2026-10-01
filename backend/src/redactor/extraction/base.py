"""Extractor protocol and OCR settings (plan §4.2)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

import yaml

from ..core.types import PageExtraction
from ..paths import config_dir


@dataclass(frozen=True)
class OcrSettings:
    """One rung of the retry ladder (plan §4.11): resolution, segmentation mode, preprocessing."""
    dpi: int = 300
    psm: int = 3
    deskew: bool = False
    contrast: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Extractor(Protocol):
    id: str

    def fingerprint(self) -> str: ...

    def extract(self, document_id: str, pdf_path: Path, page: int, scope: str,
                settings: OcrSettings | None = None) -> PageExtraction: ...


@lru_cache(maxsize=4)
def ocr_config(path: str | None = None) -> dict[str, Any]:
    p = Path(path) if path else config_dir() / "extraction" / "ocr.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8"))
