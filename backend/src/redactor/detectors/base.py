"""Detector protocol and registry (plan §4.2). Evaluation imports only these types."""

from __future__ import annotations

from typing import Any, Callable, Mapping, Protocol, Sequence, runtime_checkable

from ..core.types import DocumentText, EntitySpan


@runtime_checkable
class Detector(Protocol):
    name: str
    version: str

    def fingerprint(self) -> str: ...

    def detect(self, doc: DocumentText) -> list[EntitySpan]: ...


class MatterAware(Protocol):
    """Optional first pass over every document of a matter (plan §4.1 two-pass processing)."""

    def prepare(self, docs: Sequence[DocumentText]) -> None: ...


_REGISTRY: dict[str, Callable[[Mapping[str, Any]], Detector]] = {}


def register_detector(name: str):
    def deco(factory: Callable[[Mapping[str, Any]], Detector]):
        _REGISTRY[name] = factory
        return factory
    return deco


def create(name: str, config: Mapping[str, Any] | None = None) -> Detector:
    _ensure_builtin()
    if name not in _REGISTRY:
        raise KeyError(f"unknown detector {name}")
    return _REGISTRY[name](config or {})


def available() -> list[str]:
    _ensure_builtin()
    return sorted(_REGISTRY)


def _ensure_builtin() -> None:
    from .baseline import detector as _baseline  # noqa: F401 - registers baseline and baseline_v1
    from . import oracle as _oracle  # noqa: F401 - registers gold_oracle (evaluation only)
    from . import scans as _scans  # noqa: F401 - registers audit_scans (recall audit only)
    import importlib
    for mod in ("presidio_adapter", "openredaction_adapter", "philter_adapter"):   # candidates (C1); each optional
        try:
            importlib.import_module(f".adapters.{mod}", __package__)
        except ImportError:
            pass


def run_detector(det: Detector, docs: Sequence[DocumentText]) -> dict[str, list[EntitySpan]]:
    if hasattr(det, "prepare"):
        det.prepare(docs)  # type: ignore[attr-defined]
    return {d.document_id: sorted(det.detect(d), key=EntitySpan.sort_key) for d in docs}
