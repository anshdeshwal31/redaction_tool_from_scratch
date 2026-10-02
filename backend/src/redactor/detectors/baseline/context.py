"""Context rules (plan §7.1): keyword windows for context-only identifiers, date of birth and the
`date_role` of each date. Checksum-validated identifiers also live here."""

from __future__ import annotations

import re
from typing import Iterable, Mapping, Sequence

from . import validators as idv
from .patterns import DATE_RULES, parse_date
from .resolve import TIER_CHECKSUM, TIER_CONTEXT, Cand

LABEL_TAIL = r"\s*(?:No\.?|Number|No:|Num\.?|#)?\s*[:.\-–]?\s*"

CONTEXT_RULES: list[tuple[str, str, re.Pattern, re.Pattern]] = [
    ("CLAIM_NUMBER", "ctx.claim_number",
     re.compile(r"\b(?:WorkCover\s+)?(?:Claim|Our\s+Ref(?:erence)?|Your\s+Ref(?:erence)?|Ref(?:erence)?|File|Matter)" + LABEL_TAIL, re.I),
     re.compile(r"(?=[A-Z0-9/\-]*\d[A-Z0-9/\-]*\d[A-Z0-9/\-]*\d)[A-Z0-9][A-Z0-9/\-]{3,19}\b")),
    ("COURT_FILE_NUMBER", "ctx.court_file",
     re.compile(r"\b(?:Court\s+File|Proceeding|Action|Plaint)" + LABEL_TAIL, re.I),
     re.compile(r"[A-Z]{0,4}\s?\d{1,6}\s?(?:/|of)\s?\d{2,4}\b")),
    ("PASSPORT", "ctx.passport", re.compile(r"\bPassport" + LABEL_TAIL, re.I), re.compile(r"[A-Z]{1,2}\d{7}\b")),
    ("DRIVER_LICENCE", "ctx.driver_licence",
     re.compile(r"\b(?:Driver'?s?\s+)?Licen[cs]e" + LABEL_TAIL, re.I),
     re.compile(r"(?=(?:[A-Z]*\d){5})[A-Z0-9]{6,10}\b")),
    ("POLICY_NUMBER", "ctx.policy", re.compile(r"\bPolicy" + LABEL_TAIL, re.I),
     re.compile(r"(?=[A-Z0-9-]*\d{3})[A-Z0-9][A-Z0-9-]{4,19}\b")),
    ("CENTRELINK_CRN", "ctx.crn", re.compile(r"\b(?:CRN|Customer\s+Reference\s+Number|Centrelink(?:\s+Reference)?)" + LABEL_TAIL, re.I),
     re.compile(r"\d{3}\s?\d{3}\s?\d{3}[A-Z]\b")),
    ("MEDICAL_RECORD_NUMBER", "ctx.mrn",
     re.compile(r"\b(?:MRN|URN|UR\s?No|U\.R\.|Medical\s+Record(?:\s+Number)?|Hospital\s+(?:No|Number))" + LABEL_TAIL, re.I),
     re.compile(r"[A-Z]?\d{5,10}\b")),
    ("VEHICLE_REGISTRATION", "ctx.rego",
     re.compile(r"\b(?:Vehicle\s+)?(?:Registration|Rego)(?:\s+Plate)?" + LABEL_TAIL, re.I),
     re.compile(r"(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{2,3}[\s-]?[A-Z0-9]{2,3}\b")),
    ("DVA_NUMBER", "ctx.dva", re.compile(r"\bDVA(?:\s+File)?" + LABEL_TAIL, re.I),
     re.compile(r"[A-Z]{1,3}\s?\d{4,6}[A-Z]?\b")),
    ("ACCOUNT_NUMBER", "ctx.bsb", re.compile(r"\bBSB" + LABEL_TAIL, re.I), re.compile(r"\d{3}[\s-]?\d{3}\b")),
    ("ACCOUNT_NUMBER", "ctx.account", re.compile(r"\b(?:Account|Acct|A/C)" + LABEL_TAIL, re.I),
     re.compile(r"\d[\d\s-]{4,14}\d\b")),
]

CHECKSUM_RULES = [
    # type, rule, candidate regex, validator, context keyword regex, allowed without context
    ("TFN", "cs.tfn", re.compile(r"(?<!\d)\d{3}[\s-]?\d{3}[\s-]?\d{2,3}(?!\d)"), "tfn",
     re.compile(r"\b(?:TFN|Tax\s+File)", re.I), False),
    ("ABN", "cs.abn", re.compile(r"(?<!\d)\d{2}\s?\d{3}\s?\d{3}\s?\d{3}(?!\d)"), "abn", re.compile(r"\bABN\b", re.I), True),
    ("ACN", "cs.acn", re.compile(r"(?<!\d)\d{3}\s?\d{3}\s?\d{3}(?!\d)"), "acn", re.compile(r"\bACN\b", re.I), False),
    ("MEDICARE", "cs.medicare", re.compile(r"(?<!\d)[2-6]\d{3}\s?\d{5}\s?\d(?:\s?[-/]?\s?\d)?(?!\d)"), "medicare",
     re.compile(r"\bMedicare", re.I), False),
    ("PROVIDER_NUMBER", "cs.provider", re.compile(r"\b\d{6}\s?[0-9A-HJ-NP-RT-Y][A-HJKLTWXY]\b"), "provider_number",
     re.compile(r"\bProvider", re.I), True),
    ("IHI", "cs.ihi", re.compile(r"(?<!\d)8003\s?60\d{2}\s?\d{4}\s?\d{4}(?!\d)"), "ihi", re.compile(r"\bIHI\b", re.I), True),
]

DOB_KEYWORDS = re.compile(r"\b(?:D\.?\s?O\.?\s?B\.?|Date\s+of\s+Birth|Birth\s*date|Born(?:\s+on)?)\b\s*[:.\-–]?\s*", re.I)


def _window_after(text: str, pos: int, window: int) -> tuple[int, str]:
    """Text after a keyword: the rest of its line, plus the next line when the rest is empty."""
    end = min(len(text), pos + window)
    chunk = text[pos:end]
    nl = chunk.find("\n")
    if nl != -1:
        head = chunk[:nl]
        if head.strip():
            chunk = head
        else:
            nxt = chunk[nl + 1:]
            nl2 = nxt.find("\n")
            chunk = chunk[:nl + 1 + (nl2 if nl2 != -1 else len(nxt))]
    return pos, chunk


def context_candidates(text: str, window: int) -> list[Cand]:
    out = []
    for etype, rule_id, kw_rx, val_rx in CONTEXT_RULES:
        for kw in kw_rx.finditer(text):
            base, chunk = _window_after(text, kw.end(), window)
            m = val_rx.search(chunk)
            if m and not chunk[:m.start()].strip(" \n:.-–#"):
                out.append(Cand(base + m.start(), base + m.end(), etype, rule_id, TIER_CONTEXT, 0.85))
    return out


def checksum_candidates(text: str, window: int) -> list[Cand]:
    out = []
    for etype, rule_id, rx, kind, ctx_rx, standalone in CHECKSUM_RULES:
        for m in rx.finditer(text):
            if not idv.validate(kind, m.group(0)):
                continue
            line_start = text.rfind("\n", 0, m.start()) + 1
            before = text[max(line_start, m.start() - window):m.start()]
            if not before.strip() and line_start > 0:  # label on the line above (form layout)
                prev_start = text.rfind("\n", 0, line_start - 1) + 1
                before = text[max(prev_start, line_start - 1 - window):line_start - 1]
            has_ctx = bool(ctx_rx.search(before))
            if has_ctx or standalone:
                out.append(Cand(m.start(), m.end(), etype, rule_id, TIER_CHECKSUM, 0.97 if has_ctx else 0.8,
                                (("checksum", "valid"),)))
    return out


def dob_candidates(text: str, dates: Sequence[Cand], window: int) -> list[Cand]:
    out = []
    for kw in DOB_KEYWORDS.finditer(text):
        base, chunk = _window_after(text, kw.end(), window)
        for d in dates:
            if base <= d.start < base + len(chunk) and not text[kw.end():d.start].strip(" \n:.-–"):
                out.append(Cand(d.start, d.end, "DATE_OF_BIRTH", "ctx.dob", TIER_CONTEXT, 0.95, d.attrs))
                break
    return out


def date_role_for(text: str, start: int, phrases: Mapping[str, Iterable[str]], lookback: int = 60) -> str:
    line_start = text.rfind("\n", 0, start) + 1
    prev_line_start = text.rfind("\n", 0, max(0, line_start - 1)) + 1 if line_start > 0 else 0
    before = text[max(prev_line_start, start - lookback):start].lower()
    best, best_pos = "other", -1
    for role, words in phrases.items():
        for w in words:
            pos = before.rfind(w.lower())
            if pos > best_pos:
                best, best_pos = role, pos
    return best


def all_date_candidates(text: str) -> list[Cand]:
    from .patterns import date_candidates
    return date_candidates(text)


__all__ = ["context_candidates", "checksum_candidates", "dob_candidates", "date_role_for", "DATE_RULES", "parse_date"]
