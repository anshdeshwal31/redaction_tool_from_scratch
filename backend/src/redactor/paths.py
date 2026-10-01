"""Filesystem layout (plan §5). Confidential folders can live outside the repo via REDACTOR_DATA_ROOT."""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def data_root() -> Path:
    env = os.environ.get("REDACTOR_DATA_ROOT")
    return Path(env).resolve() if env else REPO_ROOT


def golden_docs_dir() -> Path:
    return data_root() / "golden_dataset_docs"


def golden_dir() -> Path:
    return data_root() / "golden_dataset"


def data_dir() -> Path:
    return data_root() / "data"


def runs_dir() -> Path:
    return data_root() / "runs"


def exports_dir() -> Path:
    return data_root() / "exports"


def config_dir() -> Path:
    return REPO_ROOT / "config"


def schemas_dir() -> Path:
    return REPO_ROOT / "schemas"


def experiments_dir() -> Path:
    return REPO_ROOT / "experiments"


def fixtures_dir() -> Path:
    return REPO_ROOT / "fixtures" / "synthetic"


def docs_dir() -> Path:
    return REPO_ROOT / "docs"
