"""docker/compose.yaml keeps the network rules of plan §2.4 (static check, no Docker needed)."""

from __future__ import annotations

import yaml

from redactor.paths import REPO_ROOT


def test_compose_network_isolation():
    c = yaml.safe_load((REPO_ROOT / "docker" / "compose.yaml").read_text(encoding="utf-8"))
    assert c["networks"]["sidecars"]["internal"] is True
    svc = c["services"]
    assert svc["philter"]["networks"] == ["sidecars"] and "ports" not in svc["philter"]
    assert "@sha256:" in svc["philter"]["image"]                       # pinned by digest
    assert svc["tesseract"]["network_mode"] == "none"
    for name, s in svc.items():
        for p in s.get("ports", []):
            assert p.startswith("127.0.0.1:"), name                     # loopback only
    assert svc["api"]["environment"]["HF_HUB_OFFLINE"] == "1" and svc["ui"]["environment"]["NEXT_TELEMETRY_DISABLED"] == "1"
