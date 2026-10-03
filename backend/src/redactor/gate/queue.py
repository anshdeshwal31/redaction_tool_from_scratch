"""Review queue (plan §4.11). Items have stable IDs (SHA-256 of their location and reason). Decisions are
appended to an append-only log (reviewer, time, reason) and are inputs to the pipeline: the same decisions
always give the same output hash (time is recorded, never hashed)."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Iterable

from ..core.canonical import sha256_hex
from ..paths import data_dir

DECISIONS = ("confirm_protect", "not_pii", "add_span", "add_region", "flag_handwriting", "accept_ocr")
CONFIDENCE_REASONS = {"low_confidence", "engine_disagreement"}
NOT_PII_REASONS = ("not_pii", "kept_public_body", "generic_term", "ocr_noise", "false_alarm")


class QueueError(ValueError):
    pass


def item_id(document_id: str, page: int | None, reason: str, start: int | None = None, end: int | None = None,
            field: str | None = None) -> str:
    return sha256_hex(f"{document_id}|{page}|{field}|{reason}|{start}|{end}")[:16]


def queue_dir(matter_id: str) -> Path:
    return data_dir() / "review" / matter_id


def load_decisions(matter_id: str, path: Path | None = None) -> list[dict[str, Any]]:
    p = path or (queue_dir(matter_id) / "decisions.jsonl")
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def validate_decision(item: dict[str, Any], decision: str, reason: str | None, payload: dict[str, Any] | None) -> None:
    if decision not in DECISIONS:
        raise QueueError("unknown decision")
    if decision == "accept_ocr" and item["reason"] not in CONFIDENCE_REASONS:
        raise QueueError("accept_ocr is allowed only for confidence triggers")
    if decision == "not_pii" and reason not in NOT_PII_REASONS:
        raise QueueError(f"not_pii needs a reason from {NOT_PII_REASONS}")
    if decision in ("add_span", "add_region") and not payload:
        raise QueueError("add_span/add_region need a payload")


def record(matter_id: str, item: dict[str, Any], decision: str, *, reviewer: str, reason: str | None = None,
           payload: dict[str, Any] | None = None, path: Path | None = None) -> dict[str, Any]:
    if not reviewer:
        raise QueueError("a decision needs a reviewer")
    validate_decision(item, decision, reason, payload)
    rec = {"item_id": item["item_id"], "decision": decision, "reason": reason, "payload": payload or {}, "reviewer": reviewer,
           "time": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
    p = path or (queue_dir(matter_id) / "decisions.jsonl")
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(rec, sort_keys=True) + "\n")
    return rec


def latest(decisions: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The last decision per item (the log is append-only; later decisions supersede earlier ones)."""
    out: dict[str, dict[str, Any]] = {}
    for d in decisions:
        out[d["item_id"]] = {k: v for k, v in d.items() if k != "time"}
    return out


def resolves(item: dict[str, Any], decision: dict[str, Any] | None) -> bool:
    if decision is None:
        return False
    if decision["decision"] == "flag_handwriting":
        return False
    if decision["decision"] == "accept_ocr":
        return item["reason"] in CONFIDENCE_REASONS
    return True
