"""The golden dataset's own local-only git repository (plan §2.3): its pre-push hook always fails.

The assistant never commits here; the owner commits and tags dataset versions.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ..paths import golden_dir

PRE_PUSH = "#!/bin/sh\n# golden_dataset is local-only (plan §2.3): pushing is always refused.\necho 'pre-push: golden_dataset is local-only; push refused.' >&2\nexit 1\n"


def ensure_local_repo(root: Path | None = None) -> dict[str, object]:
    root = root or golden_dir()
    root.mkdir(parents=True, exist_ok=True)
    created = False
    if not (root / ".git").exists():
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        created = True
    hook = root / ".git" / "hooks" / "pre-push"
    hook.write_text(PRE_PUSH, encoding="utf-8", newline="\n")
    remotes = subprocess.run(["git", "-C", str(root), "remote"], capture_output=True, text=True).stdout.split()
    return {"golden_repo_created": created, "pre_push_blocks": True, "remotes": len(remotes)}
