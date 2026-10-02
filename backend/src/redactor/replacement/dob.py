"""DATE_OF_BIRTH: age-preserving surrogate (plan §4.9.2).

age(B, R) = completed years; a 29 February birthday counts as 1 March in non-leap years, and 29
February is never issued. The feasible set F holds every date B' in B's year with age(B', R) = age(B, R)
for every reference date R, B' != B. The surrogate is F[HMAC(key, "DOB" | canonical | ISO(B)) mod |F|],
issued once and frozen in the vault. |F| < min_window_days falls back to the [DATE_OF_BIRTH] token (REVIEW).
A later reference date that breaks a frozen surrogate raises DOB_AGE_CONFLICT.
"""

from __future__ import annotations

import calendar
import datetime as dt
import re
from dataclasses import dataclass

from .keys import hmac_int

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m} | {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
MONTHS["sept"] = 9
TOKEN = "[DATE_OF_BIRTH]"


def _bday(b: dt.date, year: int) -> tuple[int, int]:
    if b.month == 2 and b.day == 29 and not calendar.isleap(year):
        return (3, 1)
    return (b.month, b.day)


def age(b: dt.date, r: dt.date) -> int:
    return r.year - b.year - (1 if (r.month, r.day) < _bday(b, r.year) else 0)


def feasible(b: dt.date, refs: list[dt.date]) -> list[dt.date]:
    refs = [r for r in refs if r >= b]
    want = [age(b, r) for r in refs]
    out = []
    d = dt.date(b.year, 1, 1)
    while d.year == b.year:
        if d != b and not (d.month == 2 and d.day == 29) and all(age(d, r) == a for r, a in zip(refs, want)):
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def issue(key: bytes, canonical: str, b: dt.date, refs: list[dt.date], min_window_days: int = 14) -> dt.date | None:
    f = feasible(b, refs)
    if len(f) < min_window_days:
        return None
    return f[hmac_int(key, "DOB", canonical, b.isoformat()) % len(f)]


def conflicts(b: dt.date, surrogate: dt.date, refs: list[dt.date]) -> bool:
    return any(age(b, r) != age(surrogate, r) for r in refs if r >= b)


@dataclass(frozen=True)
class DateForm:
    kind: str      # numeric | named_dmy | named_mdy | month_year | year
    date: dt.date
    sep: str = "/"
    widths: tuple[int, int, int] = (2, 2, 4)
    ordinal: bool = False
    month_style: str = "full"     # full | abbr | abbr_dot
    day_pad: bool = False
    caps: bool = False


_NUM = re.compile(r"^\s*(\d{1,2})([/.\-])(\d{1,2})\2(\d{2}|\d{4})\s*$")
_DMY = re.compile(r"^\s*(\d{1,2})(st|nd|rd|th)?\s+([A-Za-z]+)\.?,?\s+(\d{4})\s*$")
_MDY = re.compile(r"^\s*([A-Za-z]+)\.?\s+(\d{1,2})(st|nd|rd|th)?,?\s+(\d{4})\s*$")
_MY = re.compile(r"^\s*([A-Za-z]+)\.?\s+(\d{4})\s*$")
_Y = re.compile(r"^\s*(\d{4})\s*$")


def _year(y: str) -> int:
    v = int(y)
    return v if len(y) == 4 else (1900 + v if v > 30 else 2000 + v)


def _style(m: str) -> str:
    return "full" if m.lower() in {x.lower() for x in calendar.month_name if x} else "abbr"


def parse(text: str) -> DateForm | None:
    try:
        if m := _NUM.match(text):
            d, sep, mo, y = m.groups()
            return DateForm("numeric", dt.date(_year(y), int(mo), int(d)), sep, (len(d), len(mo), len(y)))
        if m := _DMY.match(text):
            d, suf, mon, y = m.groups()
            if mon.lower() not in MONTHS:
                return None
            return DateForm("named_dmy", dt.date(int(y), MONTHS[mon.lower()], int(d)), ordinal=bool(suf),
                            month_style=_style(mon), day_pad=len(d) == 2, caps=mon.isupper())
        if m := _MDY.match(text):
            mon, d, suf, y = m.groups()
            if mon.lower() not in MONTHS:
                return None
            return DateForm("named_mdy", dt.date(int(y), MONTHS[mon.lower()], int(d)), ordinal=bool(suf),
                            month_style=_style(mon), day_pad=len(d) == 2, caps=mon.isupper())
        if m := _MY.match(text):
            mon, y = m.groups()
            if mon.lower() not in MONTHS:
                return None
            return DateForm("month_year", dt.date(int(y), MONTHS[mon.lower()], 1), month_style=_style(mon), caps=mon.isupper())
        if m := _Y.match(text):
            return DateForm("year", dt.date(int(m.group(1)), 1, 1))
    except ValueError:
        return None
    return None


def _ord(n: int) -> str:
    return "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def render(form: DateForm, d: dt.date) -> str:
    """Re-render a surrogate date in the mention's own format and granularity."""
    mname = calendar.month_name[d.month] if form.month_style == "full" else calendar.month_abbr[d.month]
    if form.caps:
        mname = mname.upper()
    day = f"{d.day:02d}" if form.day_pad else str(d.day)
    if form.ordinal:
        day += _ord(d.day)
    if form.kind == "numeric":
        wd, wm, wy = form.widths
        y = f"{d.year:04d}" if wy == 4 else f"{d.year % 100:02d}"
        return f"{d.day:0{wd}d}{form.sep}{d.month:0{wm}d}{form.sep}{y}"
    if form.kind == "named_dmy":
        return f"{day} {mname} {d.year}"
    if form.kind == "named_mdy":
        return f"{mname} {day}, {d.year}"
    if form.kind == "month_year":
        return f"{mname} {d.year}"
    return str(d.year)   # year-only mentions are untouched: the year is kept
