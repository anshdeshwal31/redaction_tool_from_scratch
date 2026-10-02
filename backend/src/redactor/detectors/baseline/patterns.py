"""Regex catalogue (plan §7.1): IDs, priorities, required-context flags. Deterministic, no ML."""

from __future__ import annotations

import datetime as _dt
import re

from .resolve import TIER_PATTERN, Cand

MONTHS_FULL = ["january", "february", "march", "april", "may", "june", "july", "august", "september",
               "october", "november", "december"]
MONTH_NUM = {m: i + 1 for i, m in enumerate(MONTHS_FULL)}
MONTH_NUM.update({m[:3]: i + 1 for i, m in enumerate(MONTHS_FULL)})
MONTH_NUM["sept"] = 9
MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec"

DATE_RULES = [
    ("date.dmy_numeric", re.compile(r"(?<![\d/.-])(?P<d>0?[1-9]|[12]\d|3[01])(?P<sep>[./-])(?P<m>0?[1-9]|1[0-2])(?P=sep)(?P<y>(?:19|20)\d{2}|\d{2})(?![\d])(?!(?P=sep)\d)")),
    ("date.d_month_y", re.compile(rf"\b(?P<d>0?[1-9]|[12]\d|3[01])(?:st|nd|rd|th)?\s+(?:of\s+)?(?P<mon>{MONTHS})\.?,?\s+(?P<y>(?:19|20)\d{{2}})\b", re.I)),
    ("date.month_d_y", re.compile(rf"\b(?P<mon>{MONTHS})\.?\s+(?P<d>0?[1-9]|[12]\d|3[01])(?:st|nd|rd|th)?,?\s+(?P<y>(?:19|20)\d{{2}})\b", re.I)),
    ("date.month_y", re.compile(rf"\b(?P<mon>{MONTHS})\.?,?\s+(?P<y>(?:19|20)\d{{2}})\b", re.I)),
    ("date.iso", re.compile(r"\b(?P<y>(?:19|20)\d{2})-(?P<m>0[1-9]|1[0-2])-(?P<d>0[1-9]|[12]\d|3[01])\b")),
    ("date.yyyymmdd", re.compile(r"(?<![\d])(?P<y>(?:19|20)\d{2})(?P<m>0[1-9]|1[0-2])(?P<d>0[1-9]|[12]\d|3[01])(?![\d])")),
]

PHONE_RULES = [
    ("phone.mobile", "mobile", re.compile(r"(?<![\d+])(?:\+?61[\s-]?4|\(?04)\d{2}\)?[\s-]?\d{3}[\s-]?\d{3}(?!\d)")),
    ("phone.landline", "landline", re.compile(r"(?<![\d+])(?:\+?61[\s-]?\(?0?[2378]\)?|\(0[2378]\)|0[2378])[\s-]?\d{4}[\s-]?\d{4}(?!\d)")),
    ("phone.1300", "1300", re.compile(r"(?<!\d)1300[\s-]?\d{3}[\s-]?\d{3}(?!\d)")),
    ("phone.1800", "1800", re.compile(r"(?<!\d)1800[\s-]?\d{3}[\s-]?\d{3}(?!\d)")),
    ("phone.13", "13", re.compile(r"(?<!\d)13[\s-]?\d{2}[\s-]?\d{2}(?!\d)")),
]

EMAIL = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b")
URL = re.compile(r"\b(?:https?://|www\.)[^\s<>()\"']+[^\s<>()\"'.,;:]")
AHPRA = re.compile(r"\b(?:MED|PSY|PHY|NMW|DEN|OCC|CHI|OST|PHA|OPT|POD|MRP|ATS|CMR)\d{10}\b")
AGE_RULES = [
    ("age.year_old", re.compile(r"\b(?P<age>\d{1,3})[\s-](?:year|yr)s?[\s-]old\b", re.I)),
    ("age.years_of_age", re.compile(r"\b(?P<age>\d{1,3})\s+years?\s+of\s+age\b", re.I)),
    ("age.aged", re.compile(r"(?<=\baged\s)(?P<age>\d{1,3})\b", re.I)),
    ("age.label", re.compile(r"(?<=\bAge:\s)(?P<age>\d{1,3})\b", re.I)),
]

STREET_TYPES = ("Street|St|Road|Rd|Avenue|Ave|Drive|Dr|Court|Ct|Place|Pl|Crescent|Cres|Parade|Pde|Terrace|Tce|"
                "Highway|Hwy|Lane|Ln|Close|Cl|Boulevard|Blvd|Way|Circuit|Cct|Esplanade|Esp|Grove|Gr|Square|Sq|"
                "Parkway|Pkwy|Mews|Rise|Row|Track|Trail|Walk|Promenade|Loop|Gardens|Heights|Vista|View|Avenue")
STATE_ABBR = "QLD|NSW|VIC|TAS|SA|WA|NT|ACT"
STATE_NAMES = "Queensland|New South Wales|Victoria|Tasmania|South Australia|Western Australia|Northern Territory|Australian Capital Territory"
_CAPWORD = r"[A-Z][A-Za-z'’.-]+"
_TAIL = rf"(?:,?\s+(?:{_CAPWORD}\s+){{0,3}}?(?:{STATE_ABBR}|{STATE_NAMES})\b(?:,?\s+\d{{4}})?)?"
ADDRESS_RULES = [
    ("address.street", re.compile(
        rf"\b(?:(?:Unit|U|Apartment|Apt|Level|Lvl|Suite|Shop|Flat)\s*\d+[A-Za-z]?\s*[,/]?\s*)?"
        rf"\d{{1,5}}[A-Za-z]?(?:\s*[-–/]\s*\d{{1,5}}[A-Za-z]?)?\s+(?:{_CAPWORD}\s+){{1,3}}(?i:{STREET_TYPES})\b\.?{_TAIL}")),
    ("address.po_box", re.compile(
        rf"\b(?:P\.?\s?O\.?\s?Box|Post Office Box|Locked Bag|GPO Box|PMB)\s+\d+[A-Za-z]?{_TAIL}", re.I)),
    ("address.locality", re.compile(rf"\b(?:{_CAPWORD}\s+){{1,3}}(?:{STATE_ABBR})\s+\d{{4}}\b")),
]


def parse_date(match: re.Match) -> tuple[str | None, str]:
    """ISO date (or year-month) and granularity from a date match; None when not a real date."""
    g = match.groupdict()
    try:
        year = int(g["y"])
        if year < 100:
            year += 2000 if year <= 40 else 1900
        month = int(g["m"]) if g.get("m") else MONTH_NUM[g["mon"].lower().rstrip(".")]
        if g.get("d"):
            day = int(g["d"])
            return _dt.date(year, month, day).isoformat(), "full"
        return f"{year:04d}-{month:02d}", "month_year"
    except (ValueError, KeyError):
        return None, ""


def date_candidates(text: str) -> list[Cand]:
    out = []
    for rule_id, rx in DATE_RULES:
        for m in rx.finditer(text):
            iso, gran = parse_date(m)
            if iso is None:
                continue
            out.append(Cand(m.start(), m.end(), "DATE", rule_id, TIER_PATTERN, 0.9, (("granularity", gran), ("iso", iso))))
    return out


def phone_candidates(text: str) -> list[Cand]:
    out = []
    for rule_id, cls, rx in PHONE_RULES:
        for m in rx.finditer(text):
            out.append(Cand(m.start(), m.end(), "PHONE", rule_id, TIER_PATTERN, 0.9, (("number_class", cls),)))
    return out


def simple_candidates(text: str) -> list[Cand]:
    out = [Cand(m.start(), m.end(), "EMAIL", "email", TIER_PATTERN, 0.95) for m in EMAIL.finditer(text)]
    for m in URL.finditer(text):
        out.append(Cand(m.start(), m.end(), "URL", "url", TIER_PATTERN, 0.9))
    for m in AHPRA.finditer(text):
        out.append(Cand(m.start(), m.end(), "AHPRA_REGISTRATION", "ahpra", TIER_PATTERN, 0.95))
    for rule_id, rx in AGE_RULES:
        for m in rx.finditer(text):
            age = int(m.group("age"))
            if 0 < age < 120:
                out.append(Cand(m.start(), m.end(), "AGE", rule_id, TIER_PATTERN, 0.85, (("stated_age", str(age)),)))
    for rule_id, rx in ADDRESS_RULES:
        for m in rx.finditer(text):
            s, e = m.start(), m.end()
            while e > s and text[e - 1] in " ,\n":
                e -= 1
            out.append(Cand(s, e, "ADDRESS", rule_id, TIER_PATTERN, 0.8))
    return out
