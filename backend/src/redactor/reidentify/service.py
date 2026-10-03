"""File and API entry points for re-identification, with the audit log (plan §4.12.4).

The restored text holds real PII: it is written only under exports/<matter>/reidentified/ (git-ignored)
and never passes through any external process. Every call appends one line to
data/audit/reidentify.jsonl with counts and hashes only.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from ..core.canonical import sha256_hex
from ..paths import data_dir, exports_dir
from ..replacement.pools import load_pools
from ..replacement.vault import Vault, vault_path
from .core import build_index, reidentify


class ReidentifyError(RuntimeError):
    pass


def audit_path() -> Path:
    return data_dir() / "audit" / "reidentify.jsonl"


def reidentified_dir(matter_id: str) -> Path:
    return exports_dir() / matter_id / "reidentified"


def _pool_names() -> set[str]:
    p = load_pools()
    return set(p.surnames) | set(p.female) | set(p.male) | set(p.unisex)


def restore_text(matter_id: str, text: str, *, actor: str, vault: Vault | None = None, channel: str = "cli") -> tuple[str, dict[str, Any]]:
    own = vault is None
    if own and not vault_path(matter_id).exists():
        raise ReidentifyError("no vault for this matter")
    vault = vault or Vault.open(matter_id)
    try:
        idx = build_index(vault, pool_names=_pool_names())
        restored, rep = reidentify(text, idx)
    finally:
        if own:
            vault.close()
    report = rep.to_dict()
    entry = {"time": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "matter_id": matter_id, "actor": actor,
             "channel": channel, "input_sha256": sha256_hex(text), "output_sha256": sha256_hex(restored), "report": report}
    audit_path().parent.mkdir(parents=True, exist_ok=True)
    with audit_path().open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n")
    return restored, report


def restore_file(matter_id: str, in_path: Path, out_name: str, *, actor: str) -> dict[str, Any]:
    out_dir = reidentified_dir(matter_id)
    name = Path(out_name).name
    if not name or name != out_name.replace("\\", "/").split("/")[-1]:
        raise ReidentifyError("--out is a file name; output is always written under exports/<matter>/reidentified/")
    restored, report = restore_text(matter_id, in_path.read_text(encoding="utf-8"), actor=actor)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / name).write_text(restored, encoding="utf-8", newline="\n")
    return {"matter_id": matter_id, "output": f"exports/{matter_id}/reidentified/{name}", "output_sha256": sha256_hex(restored), **report}
