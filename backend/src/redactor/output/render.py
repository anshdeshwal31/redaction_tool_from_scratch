"""Text and Markdown renders (plan §4.12.2): pure, deterministic functions of the JSON. Tokens such as
[NATIONALITY] pass through; a render never adds information that is not in the JSON."""

from __future__ import annotations

from typing import Any, Mapping

PAGE_SEPARATOR = "\n\n\f\n\n"


def render_text(doc: Mapping[str, Any]) -> str:
    return PAGE_SEPARATOR.join(p["text"] for p in sorted(doc["pages"], key=lambda p: p["page"])) + "\n"


def render_markdown(doc: Mapping[str, Any]) -> str:
    parts = [f"# {doc['document_id']}", ""]
    for p in sorted(doc["pages"], key=lambda p: p["page"]):
        parts += [f"## Page {p['page']}", "", p["text"].replace("\n", "  \n"), ""]
    return "\n".join(parts).rstrip() + "\n"
