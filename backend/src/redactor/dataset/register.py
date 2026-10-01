"""`dataset register` (plan §8): safe IDs, SHA-256 hashes, read-only copies, manifest.

Output is aggregate only: counts, IDs and hash prefixes. Original filenames are stored only in
the confidential metadata files (golden_dataset/metadata/), never printed.
"""

from __future__ import annotations

import os
import shutil
import stat
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from ..core.canonical import read_json, sha256_file, sha256_hex, write_canonical
from ..paths import config_dir, golden_dir, golden_docs_dir
from .models import Manifest, ManifestDocument


class RegisterError(RuntimeError):
    pass


@lru_cache(maxsize=2)
def dataset_config(path: str | None = None) -> dict[str, Any]:
    p = Path(path) if path else config_dir() / "dataset.v0.1.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def manifest_path() -> Path:
    return golden_dir() / "manifest.json"


def load_manifest() -> Manifest:
    p = manifest_path()
    return Manifest.model_validate(read_json(p)) if p.exists() else Manifest()


def save_manifest(m: Manifest) -> str:
    return write_canonical(manifest_path(), m.dump())


def document_path(document_id: str) -> Path:
    return golden_dir() / "documents" / f"{document_id}.pdf"


def split_for(document_id: str, cfg: dict[str, Any]) -> str:
    for split, ids in cfg.get("splits", {}).items():
        if document_id in ids:
            return split
    return cfg.get("default_split", "dev")


def _make_read_only(p: Path) -> None:
    os.chmod(p, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)


@dataclass
class RegisterResult:
    documents: int = 0
    new: list[str] = field(default_factory=list)
    already_registered: list[str] = field(default_factory=list)
    sha256_prefixes: dict[str, str] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {"documents": self.documents, "new": self.new, "already_registered": self.already_registered,
                "sha256_prefixes": self.sha256_prefixes}


def register(source_dir: Path | None = None, *, enforce_expected: bool = True) -> RegisterResult:
    source_dir = source_dir or golden_docs_dir()
    cfg = dataset_config()
    expected = cfg.get("expected_sha256_prefixes", {}) if enforce_expected else {}
    files = sorted((p for p in source_dir.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"),
                   key=lambda p: p.name)
    manifest = load_manifest()
    by_sha = {d.sha256: d for d in manifest.documents}
    by_name = {d.source_name_sha256: d for d in manifest.documents}
    next_n = max((int(d.document_id.split("_")[1]) for d in manifest.documents), default=0) + 1
    result = RegisterResult()
    for f in files:
        sha = sha256_file(f)
        name_hash = sha256_hex(f.name)
        existing = by_sha.get(sha)
        if existing is None and name_hash in by_name:
            raise RegisterError(f"{by_name[name_hash].document_id}: source file changed since registration")
        if existing is not None:
            copy = document_path(existing.document_id)
            if not copy.exists():
                shutil.copyfile(f, copy)
                _make_read_only(copy)
            if sha256_file(copy) != existing.sha256:
                raise RegisterError(f"{existing.document_id}: registered copy does not match its hash")
            result.already_registered.append(existing.document_id)
            result.sha256_prefixes[existing.document_id] = existing.sha256[:12]
            continue
        doc_id = f"doc_{next_n:03d}"
        if doc_id in expected and not sha.startswith(expected[doc_id]):
            raise RegisterError(f"{doc_id}: hash prefix differs from the one recorded in the plan (§1.2)")
        next_n += 1
        copy = document_path(doc_id)
        copy.parent.mkdir(parents=True, exist_ok=True)
        if copy.exists():
            os.chmod(copy, stat.S_IWRITE | stat.S_IREAD)
        shutil.copyfile(f, copy)
        if sha256_file(copy) != sha:
            raise RegisterError(f"{doc_id}: copy hash mismatch")
        _make_read_only(copy)
        doc = ManifestDocument(document_id=doc_id, sha256=sha, size_bytes=f.stat().st_size,
                               matter_id=cfg["matter_id"], split=split_for(doc_id, cfg),
                               registered_order=len(manifest.documents) + 1, source_name_sha256=name_hash)
        manifest.documents.append(doc)
        by_sha[sha] = doc
        result.new.append(doc_id)
        result.sha256_prefixes[doc_id] = sha[:12]
    if "EX-001" not in manifest.exceptions:
        manifest.exceptions.append("EX-001")
    manifest.documents.sort(key=lambda d: d.document_id)
    manifest.files.update({f"documents/{d.document_id}.pdf": d.sha256 for d in manifest.documents})
    save_manifest(manifest)
    result.documents = len(manifest.documents)
    return result


def source_file_for(document_id: str, source_dir: Path | None = None) -> Path | None:
    """Locate the original file for a registered document by hash (the name is never stored in the manifest)."""
    source_dir = source_dir or golden_docs_dir()
    manifest = load_manifest()
    doc = next((d for d in manifest.documents if d.document_id == document_id), None)
    if doc is None:
        return None
    for f in sorted(source_dir.iterdir(), key=lambda p: p.name):
        if f.is_file() and sha256_hex(f.name) == doc.source_name_sha256:
            return f
    return None


def verify_hashes(source_dir: Path | None = None) -> dict[str, Any]:
    """Checksum guard (plan §8 item 4): originals and registered copies are unchanged."""
    source_dir = source_dir or golden_docs_dir()
    manifest = load_manifest()
    problems: list[str] = []
    for d in manifest.documents:
        copy = document_path(d.document_id)
        if not copy.exists() or sha256_file(copy) != d.sha256:
            problems.append(f"{d.document_id}:copy")
        src = source_file_for(d.document_id, source_dir)
        if src is None or sha256_file(src) != d.sha256:
            problems.append(f"{d.document_id}:source")
    return {"documents": len(manifest.documents), "ok": not problems, "problems": problems}
