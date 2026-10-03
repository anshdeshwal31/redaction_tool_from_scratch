"""OpenRedaction candidate (plan §11 C1): the `openredaction` npm package 1.1.5 (pinned in
sidecars/openredaction/pnpm-lock.yaml), "as shipped" with its default options.

It runs in a local Node process (sidecars/openredaction/runner.js) that installs a network guard before
the library loads (sockets, TLS, HTTP(S), DNS and fetch all throw) and forces off the library's learning
store (which would otherwise write a whitelist file into the working directory), caching, audit, metrics
and NER. The hosted AI assist is not part of this package version and is never configured. Texts go in
over stdin; offsets (UTF-16 code units, converted here) come back, never the values.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any, Mapping, Sequence

from ...core.canonical import sha256_file
from ..base import register_detector
from .common import AdapterBase, Raw, repo_path, utf16_to_cp


class OpenRedactionError(RuntimeError):
    pass


class OpenRedactionAdapter(AdapterBase):
    name = "openredaction"
    version = "0.1.0"
    label_map_name = "openredaction"

    def __init__(self, config: Mapping[str, Any] | None = None):
        super().__init__({"options": {}, "batch": 25, **dict(config or {})})
        self._node = None

    def _node_version(self) -> str:
        if self._node is None:
            node = shutil.which("node")
            if node is None:
                raise OpenRedactionError("node is not installed")
            self._node = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip()
        return self._node

    def engine_fingerprint(self) -> dict[str, Any]:
        d = repo_path("sidecars", "openredaction")
        return {"package": "openredaction@1.1.5", "lock": sha256_file(d / "pnpm-lock.yaml")[:16],
                "runner": sha256_file(d / "runner.js")[:16], "node": self._node_version()}

    def analyze(self, texts: Sequence[str]) -> list[list[Raw]]:
        d = repo_path("sidecars", "openredaction")
        if not (d / "node_modules" / "openredaction").exists():
            raise OpenRedactionError("sidecar not installed (pnpm install in sidecars/openredaction)")
        self._node_version()
        out: list[list[Raw]] = []
        n = int(self.config["batch"])
        for i in range(0, len(texts), n):
            chunk = list(texts[i:i + n])
            req = json.dumps({"options": self.config["options"], "texts": chunk}, ensure_ascii=False)
            r = subprocess.run([shutil.which("node"), str(d / "runner.js")], input=req.encode("utf-8"), capture_output=True, cwd=str(d))
            if r.returncode != 0:
                raise OpenRedactionError(f"sidecar failed (exit {r.returncode})")   # stderr carries the error class only
            body = json.loads(r.stdout.decode("utf-8"))
            for t, dets in zip(chunk, body["results"]):
                raws = []
                for det in dets:
                    s, e = utf16_to_cp(t, [int(det["start"]), int(det["end"])])
                    if 0 <= s < e <= len(t):
                        raws.append(Raw(s, e, str(det["type"]), det.get("confidence")))
                out.append(raws)
        return out


@register_detector("openredaction")
def _make(config: Mapping[str, Any]) -> OpenRedactionAdapter:
    return OpenRedactionAdapter(config)
