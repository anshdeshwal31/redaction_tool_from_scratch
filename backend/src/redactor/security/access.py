"""Role gate for export and re-identification (config/access.v0.1.yaml)."""

from __future__ import annotations

import yaml

from ..paths import config_dir


class AccessDenied(PermissionError):
    pass


def allowed(operation: str, actor: str | None) -> bool:
    p = config_dir() / "access.v0.1.yaml"
    roles = (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("roles", {}) if p.exists() else {}
    return bool(actor) and actor in (roles.get(operation) or [])


def require(operation: str, actor: str | None) -> None:
    if not allowed(operation, actor):
        raise AccessDenied(f"actor is not authorised for {operation} (config/access.v0.1.yaml)")
