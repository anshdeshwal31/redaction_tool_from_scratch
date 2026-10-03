"""Run manifest and environment fingerprint (plan §4.8)."""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from typing import Any, Mapping

from ..core.canonical import sha256_file
from ..extraction.base import ocr_config
from ..ingest.pdfinfo import PDFIUM_VERSION
from ..paths import REPO_ROOT

THREAD_VARS = ("OMP_THREAD_LIMIT", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "ORT_NUM_THREADS")


def _git_commit() -> str | None:
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=REPO_ROOT, timeout=10)
        return r.stdout.strip() or None if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _git_dirty() -> bool | None:
    try:
        r = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], capture_output=True, text=True,
                           cwd=REPO_ROOT, timeout=10)
        return bool(r.stdout.strip()) if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def environment(extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    ocr = ocr_config()
    t = ocr["tesseract"]
    lock = REPO_ROOT / "backend" / "uv.lock"
    env = {
        "python": platform.python_version(),
        "implementation": sys.implementation.name,
        "platform": platform.system(),
        "uv_lock_sha256": sha256_file(lock) if lock.exists() else None,
        "pdfium": PDFIUM_VERSION,
        "render": ocr["render"],
        "tesseract": {"version": str(t["version"]), "leptonica": str(t["leptonica"]), "model": t["model"],
                      "model_sha256": t["model_sha256"], "image_id": t["image_id"], "base_image": t["base_image"],
                      "psm": t["psm"], "oem": t["oem"]},
        "threads": {v: os.environ.get(v) for v in THREAD_VARS},
        "pythonhashseed": os.environ.get("PYTHONHASHSEED"),
        "second_ocr_engine": _rapidocr(),
        "packages": _packages(),
        "spacy_model": _dist("en-core-web-lg"),
        "node": _cmd_version(["node", "--version"]),
        "sidecars": {"openredaction_lock_sha256": _sha_or_none(REPO_ROOT / "sidecars" / "openredaction" / "pnpm-lock.yaml"),
                     "philter_image_id": _philter_image_id(), "philter_java": "OpenJDK 17 (inside the pinned Philter image)"},
    }
    env.update(dict(extra or {}))
    return env


def _dist(name: str) -> str | None:
    from importlib.metadata import PackageNotFoundError, version
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def _packages() -> dict[str, str | None]:
    """Versions of the pinned engines that can change results (full pins are in uv.lock)."""
    return {n: _dist(n) for n in ("pypdfium2", "rapidocr-onnxruntime", "onnxruntime", "presidio-analyzer", "spacy", "rapidfuzz")}


def _rapidocr() -> dict[str, object] | None:
    try:
        from ..extraction.ocr_rapid import model_hashes
        return {"version": _dist("rapidocr-onnxruntime"), "models_sha256": model_hashes(), "threads": 1}
    except Exception:  # noqa: BLE001 - engine not installed: recorded as absent
        return None


def _sha_or_none(p) -> str | None:
    return sha256_file(p) if p.exists() else None


def _cmd_version(cmd: list[str]) -> str | None:
    import shutil
    exe = shutil.which(cmd[0])
    if exe is None:
        return None
    try:
        r = subprocess.run([exe, *cmd[1:]], capture_output=True, text=True, timeout=20)
        return r.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _philter_image_id() -> str | None:
    try:
        from ..detectors.adapters.philter_adapter import IMAGE
        r = subprocess.run(["docker", "image", "inspect", IMAGE, "--format", "{{.Id}}"], capture_output=True, text=True, timeout=30)
        return r.stdout.strip() or None
    except (OSError, subprocess.SubprocessError, ImportError):
        return None


def run_manifest(*, run_id: str, experiment: Mapping[str, Any], config_sha256: str, dataset_manifest_sha256: str | None,
                 detector_fingerprint: str, text_sources: Mapping[str, list[str]], extra_env: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {
        "schema": "redactor.run_manifest", "schema_version": "0.1.0", "run_id": run_id,
        "experiment_id": experiment.get("experiment_id"), "config_sha256": config_sha256,
        "dataset_manifest_sha256": dataset_manifest_sha256, "git_commit": _git_commit(), "git_dirty": _git_dirty(),
        "detector": detector_fingerprint, "text_sources": {k: sorted(set(v)) for k, v in sorted(text_sources.items())},
        "environment": environment(extra_env),
    }
