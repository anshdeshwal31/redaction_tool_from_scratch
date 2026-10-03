"""Private run outputs (plan §4.8): predictions and mention records with their text, and a local HTML
error browser. Written only under runs/<id>/private/, which is git-ignored and denied to the assistant."""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..core.canonical import write_canonical
from ..core.types import EntitySpan


def write_private(dir_: Path, *, preds: Mapping[str, Sequence[EntitySpan]], splits: Mapping[str, Mapping[str, Any]],
                  leak_detail: Sequence[Mapping[str, Any]]) -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    write_canonical(dir_ / "predictions.json", {d: [s.to_dict() for s in spans] for d, spans in sorted(preds.items())})
    write_canonical(dir_ / "mentions.json", {s: v for s, v in sorted(splits.items())})
    write_canonical(dir_ / "leak_detail.json", list(leak_detail))
    (dir_ / "errors.html").write_text(error_browser(preds, splits), encoding="utf-8", newline="\n")


def error_browser(preds: Mapping[str, Sequence[EntitySpan]], splits: Mapping[str, Mapping[str, Any]]) -> str:
    rows = []
    for split, data in sorted(splits.items()):
        for m in data.get("mentions", []):
            if m.get("outcome") in (None, "OK") and m.get("detected"):
                continue
            rows.append("<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                html.escape(split), html.escape(m["document_id"]), m.get("page") or m.get("field"), html.escape(m["type"]),
                html.escape(str(m.get("outcome") or ("missed" if not m.get("detected") else ""))),
                html.escape(m.get("text") or ""), html.escape(m.get("extracted") or "")))
    return ("<!doctype html><html><head><meta charset='utf-8'><title>Errors (private)</title>"
            "<style>body{font:13px sans-serif}td,th{border:1px solid #ccc;padding:3px 6px}table{border-collapse:collapse}</style>"
            "</head><body><h1>Gold mentions not detected or not protected (local only)</h1><table>"
            "<tr><th>Split</th><th>Doc</th><th>Page</th><th>Type</th><th>Outcome</th><th>Gold text</th><th>Extracted</th></tr>"
            + "".join(rows) + "</table></body></html>")
