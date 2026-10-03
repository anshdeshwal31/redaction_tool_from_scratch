"""Leak test for public artifacts (plan §2.2, §4.5): every file under runs/<id>/public/ is scanned for
gold surface strings. Methods: exact (case-insensitive, whitespace-normalised, surfaces of 4+ chars),
digits-only for identifiers (6+ digits, separators ignored), surname/given-name tokens of 3+ chars
(case-sensitive whole words) and fuzzy matching for OCR variants (surfaces of 8+ chars).

The result names files, methods and counts only. The hit details (which surface, where) go to the
caller's private sink and never into public output.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from rapidfuzz import fuzz

IDENTIFIER_TYPES = frozenset({"CLAIM_NUMBER", "COURT_FILE_NUMBER", "POLICY_NUMBER", "TFN", "ABN", "ACN", "PASSPORT",
                              "DRIVER_LICENCE", "VEHICLE_REGISTRATION", "CENTRELINK_CRN", "ACCOUNT_NUMBER", "MEDICARE",
                              "PROVIDER_NUMBER", "AHPRA_REGISTRATION", "IHI", "MEDICAL_RECORD_NUMBER", "DVA_NUMBER",
                              "PHONE", "DATE_OF_BIRTH"})
NAME_STOP = frozenset({"The", "And", "For", "Ltd", "Pty", "Limited", "Group", "Centre", "Center", "Medical", "Hospital",
                       "Clinic", "Health", "Lawyers", "College", "University", "Street", "Road", "Avenue", "Drive",
                       "Queensland", "Australia", "South", "North", "East", "West", "New", "Wales", "Services"})
FUZZY_MIN_LEN = 10
FUZZY_THRESHOLD = 92.0


@dataclass(frozen=True)
class Surface:
    text: str
    entity_type: str


def _norm(t: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", t).split()).casefold()


def _digits(t: str) -> str:
    return re.sub(r"\D", "", t)


def build_needles(surfaces: Iterable[Surface]) -> dict[str, list[str]]:
    exact, digits, tokens, fuzzy = set(), set(), set(), set()
    for s in surfaces:
        if not s.text:
            continue
        n = _norm(s.text)
        if len(n) >= 4 and (any(c.isalpha() for c in n) or len(n) >= 8):
            exact.add(n)  # short all-digit surfaces are left to the digits method
        if len(n) >= FUZZY_MIN_LEN:
            fuzzy.add(n)
        if s.entity_type in IDENTIFIER_TYPES:
            d = _digits(s.text)
            if len(d) >= 6:
                digits.add(d)
        if s.entity_type == "PERSON":
            for tok in re.findall(r"[A-Za-z][A-Za-z'\-]+", s.text):
                tok = tok.strip("'-")
                if len(tok) >= 3 and tok not in NAME_STOP and tok[0].isupper():
                    tokens.add(tok)
    return {"exact": sorted(exact), "digits": sorted(digits), "tokens": sorted(tokens), "fuzzy": sorted(fuzzy)}


def scan_text(text: str, needles: dict[str, list[str]]) -> list[tuple[str, str]]:
    """Returns (method, needle) hits. Callers must keep the needles out of public output."""
    hits: list[tuple[str, str]] = []
    norm = _norm(text)
    for n in needles["exact"]:
        if n in norm and re.search(r"(?<![0-9a-z])" + re.escape(n) + r"(?![0-9a-z])", norm):
            hits.append(("exact", n))
    if needles["digits"]:
        runs = {_digits(m.group(0)) for m in re.finditer(r"\d[\d \-/.]{4,}\d", text)}
        runs |= {d for d in re.findall(r"\d{6,}", text)}
        for d in needles["digits"]:
            if any(d in r for r in runs):
                hits.append(("digits", d))
    if needles["tokens"]:
        words = set(re.findall(r"[A-Za-z][A-Za-z'\-]+", text))
        for t in needles["tokens"]:
            if t in words:
                hits.append(("token", t))
    if needles["fuzzy"]:
        lines = [ln for ln in (_norm(x) for x in text.splitlines()) if len(ln) >= FUZZY_MIN_LEN]
        for n in needles["fuzzy"]:
            if any(n in ln for ln in lines):
                continue  # already an exact hit
            if any(fuzz.partial_ratio(n, ln) >= FUZZY_THRESHOLD for ln in lines if len(ln) >= len(n) - 2):
                hits.append(("fuzzy", n))
    return hits


def scan_dir(public_dir: Path, needles: dict[str, list[str]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Public summary (counts only) and private detail (file, method, needle)."""
    files = sorted(p for p in public_dir.rglob("*") if p.is_file())
    detail: list[dict[str, Any]] = []
    by_method: dict[str, int] = {}
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = f.read_bytes().decode("latin-1")
        for method, needle in scan_text(text, needles):
            by_method[method] = by_method.get(method, 0) + 1
            detail.append({"file": str(f.relative_to(public_dir)), "method": method, "needle": needle})
    summary = {"files_scanned": len(files), "hits": len(detail), "by_method": dict(sorted(by_method.items())),
               "needles": {k: len(v) for k, v in needles.items()}, "passed": not detail}
    return summary, detail


def surfaces_from_gold(mentions: Sequence[Any]) -> list[Surface]:
    """Gold mentions whose action protects them, plus every critical mention."""
    out = []
    for m in mentions:
        if m.text and (m.protect or m.critical):
            out.append(Surface(m.text, m.entity_type))
    return out
