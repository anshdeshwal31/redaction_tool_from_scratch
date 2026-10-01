"""Tesseract OCR in the pinned Docker image (plan §4.7, §11 F3).

Containers run with `--network none`; they read page renders from a read-only bind mount and
write TSV files to a confidential folder under data/. Nothing is printed.
"""

from __future__ import annotations

import csv
import io
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from PIL import Image

from ..core.canonical import sha256_obj
from ..core.coords import pixel_box_to_points
from ..core.types import RawWord
from .base import OcrSettings, ocr_config
from .render import RENDER_INFO, png_bytes, preprocess


class OcrError(RuntimeError):
    pass


@dataclass(frozen=True)
class TesseractConfig:
    image: str
    image_id: str
    version: str
    leptonica: str
    model: str
    model_sha256: str
    lang: str
    oem: int

    @classmethod
    def load(cls) -> "TesseractConfig":
        c = ocr_config()["tesseract"]
        return cls(c["image"], c["image_id"], str(c["version"]), str(c["leptonica"]), c["model"],
                   c["model_sha256"], c["lang"], int(c["oem"]))


def fingerprint_dict(cfg: TesseractConfig, settings: OcrSettings) -> dict[str, Any]:
    return {"engine": "tesseract", "version": cfg.version, "leptonica": cfg.leptonica, "image_id": cfg.image_id,
            "model": cfg.model, "model_sha256": cfg.model_sha256, "lang": cfg.lang, "oem": cfg.oem,
            "settings": settings.to_dict(), "render": {**RENDER_INFO, "dpi": settings.dpi, "mode": "gray"}}


def text_source_id(cfg: TesseractConfig, settings: OcrSettings) -> str:
    return f"ocr.tesseract@{cfg.version}+best-{cfg.lang}#{sha256_obj(fingerprint_dict(cfg, settings))[:8]}"


def image_available(cfg: TesseractConfig) -> bool:
    r = subprocess.run(["docker", "image", "inspect", "--format", "{{.Id}}", cfg.image], capture_output=True, text=True)
    return r.returncode == 0 and r.stdout.strip() == cfg.image_id


def run_batch(cfg: TesseractConfig, pngs: Sequence[Path], out_dir: Path, settings: OcrSettings) -> dict[Path, Path]:
    """OCR PNGs that share one directory; returns png -> tsv path. One container per batch."""
    if not pngs:
        return {}
    in_dir = pngs[0].parent
    if any(p.parent != in_dir for p in pngs):
        raise OcrError("all PNGs in a batch must share a directory")
    out_dir.mkdir(parents=True, exist_ok=True)
    names = [p.name for p in pngs]
    script = ('for f in "$@"; do b="${f%.png}"; '
              f'tesseract "/in/$f" "/out/$b" --tessdata-dir /opt/tessdata_best -l {cfg.lang} '
              f'--oem {cfg.oem} --psm {settings.psm} --dpi {settings.dpi} '
              '-c tessedit_create_tsv=1 -c tessedit_create_txt=0 >/dev/null 2>&1 || exit 3; done')
    cmd = ["docker", "run", "--rm", "--network", "none", "-e", "OMP_THREAD_LIMIT=1",
           "--mount", f"type=bind,source={in_dir.resolve()},target=/in,readonly",
           "--mount", f"type=bind,source={out_dir.resolve()},target=/out",
           cfg.image, "sh", "-c", script, "sh", *names]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise OcrError(f"tesseract container failed (exit {r.returncode})")
    out = {}
    for p in pngs:
        tsv = out_dir / (p.stem + ".tsv")
        if not tsv.exists():
            raise OcrError("tesseract produced no TSV for a page")
        out[p] = tsv
    return out


def prepare_input(src_png: Path, dst_png: Path, settings: OcrSettings) -> Path:
    """Apply retry-ladder preprocessing (if any) to a render; the plain setting reuses the render."""
    if not (settings.deskew or settings.contrast):
        return src_png
    img = Image.open(src_png)
    img.load()
    dst_png.parent.mkdir(parents=True, exist_ok=True)
    dst_png.write_bytes(png_bytes(preprocess(img, deskew=settings.deskew, contrast=settings.contrast)))
    return dst_png


def parse_tsv(tsv_text: str, dpi: int, *, offset_x_pt: float = 0.0, offset_y_pt: float = 0.0,
              block_base: int = 0) -> list[RawWord]:
    """Level-5 rows (words) in Tesseract's reading order; lines keyed by (block, paragraph, line)."""
    reader = csv.DictReader(io.StringIO(tsv_text), delimiter="\t", quoting=csv.QUOTE_NONE)
    words: list[RawWord] = []
    for row in reader:
        if row.get("level") != "5":
            continue
        text = (row.get("text") or "").strip()
        if not text:
            continue
        block = block_base + int(row["block_num"]) * 100 + int(row["par_num"])
        box = pixel_box_to_points(float(row["left"]), float(row["top"]), float(row["width"]),
                                  float(row["height"]), dpi, offset_x_pt, offset_y_pt)
        conf = float(row["conf"])
        words.append(RawWord(text, box, None if conf < 0 else round(conf, 2), block, int(row["line_num"])))
    return words
