"""Phones, emails, URLs, addresses, places and organisations (plan §4.9.3, §4.9.4)."""

from __future__ import annotations

import re

from ..detectors.baseline import validators as v
from .keys import hmac_int, norm, pick
from .pools import Pools


# ---------------------------------------------------------------- phones (ACMA reserved ranges)
def phone_class(value: str) -> tuple[str, str]:
    """Returns (class, national digits): mobile | landline | local8 | 1300 | 1800 | 13 | other."""
    d = v.digits_only(value)
    if d.startswith("61") and len(d) == 11:
        d = "0" + d[2:]
    if len(d) == 10 and d.startswith("04"):
        return "mobile", d
    if len(d) == 10 and d.startswith("1300"):
        return "1300", d
    if len(d) == 10 and d.startswith("1800"):
        return "1800", d
    if len(d) == 6 and d.startswith("13"):
        return "13", d
    if len(d) == 10 and d.startswith("0"):
        return "landline", d
    if len(d) == 8:
        return "local8", d
    return "other", d


def _render_digits(original: str, new_digits: str) -> str:
    """Put new digits into the original's layout (spaces, brackets, +61) when the counts agree."""
    od = v.digits_only(original)
    if original.strip().startswith("+61") and new_digits.startswith("0"):
        new_digits = "61" + new_digits[1:]
    if len(od) == len(new_digits):
        it = iter(new_digits)
        return "".join(next(it) if ch.isdigit() else ch for ch in original)
    return new_digits


def phone(key: bytes, value: str, pools: Pools, taken: set[str]) -> str:
    cls, d = phone_class(value)
    n = norm(d)
    if cls == "mobile":
        choice, _ = pick(key, [x for x in pools.mobile if x not in taken] or pools.mobile, "MOBILE", n)
        return _render_digits(value, choice)
    if cls in ("1300", "13"):
        choice, _ = pick(key, pools.local_rate, "1300", n)
        return _render_digits(value, choice)
    if cls == "1800":
        choice, _ = pick(key, pools.freephone, "1800", n)
        return _render_digits(value, choice)
    area = d[:2] if cls == "landline" and d[:2] in pools.area_codes else "07"
    for attempt in range(10_000):
        block = pools.landline_blocks[hmac_int(key, "BLOCK", n, str(attempt)) % len(pools.landline_blocks)]
        tail = f"{hmac_int(key, 'TAIL', n, str(attempt)) % 10000:04d}"
        digits = (area + block + tail) if cls == "landline" else (block + tail)
        out = _render_digits(value, digits)
        if out not in taken:
            return out
    raise ValueError("no free phone surrogate")


def is_reserved_phone(value: str, pools: Pools) -> bool:
    cls, d = phone_class(value)
    if cls == "mobile":
        return d in pools.mobile
    if cls in ("1300", "13"):
        return d in pools.local_rate
    if cls == "1800":
        return d in pools.freephone
    if cls == "landline":
        return d[:2] in pools.area_codes and d[2:6] in pools.landline_blocks
    if cls == "local8":
        return d[:4] in pools.landline_blocks
    return False


# ---------------------------------------------------------------- email / URL (RFC 2606)
def email(key: bytes, value: str, pools: Pools, local_hint: str | None, taken: set[str]) -> str:
    n = norm(value)
    for attempt in range(1000):
        domain = pools.email_domains[hmac_int(key, "EMAILDOM", n, str(attempt)) % len(pools.email_domains)]
        local = local_hint or f"contact{hmac_int(key, 'EMAILLOC', n, str(attempt)) % 10000:04d}"
        if attempt and local_hint:
            local = f"{local_hint}{attempt}"
        out = f"{local}@{domain}"
        if out not in taken:
            return out
    raise ValueError("no free email surrogate")


def url(key: bytes, value: str, pools: Pools) -> str:
    scheme = "https://" if value.lower().startswith("https://") else ("http://" if value.lower().startswith("http://") else "")
    www = "www." if "www." in value.lower() else ""
    path = "/" + f"p{hmac_int(key, 'URLPATH', norm(value)) % 1000:03d}" if re.search(r"\.[a-z]{2,}/\S", value) else ""
    return f"{scheme}{www}{pools.url_domain}{path}"


# ---------------------------------------------------------------- places
_STATE_RE = r"(QLD|Qld|NSW|VIC|Vic|SA|WA|TAS|Tas|NT|ACT|Queensland|New South Wales|Victoria|South Australia|Western Australia|Tasmania|Northern Territory)"
_LOCALITY = re.compile(r"(?P<suburb>[A-Za-z][A-Za-z' \-]*?)[,\s]+(?P<state>" + _STATE_RE + r")[,\s]+(?P<postcode>\d{4})\s*$")
_STREET = re.compile(r"(?P<pre>(?:(?:Unit|Suite|Level|Shop|Lot)\s*\w+[,\s]+|\w+/)*)?(?P<num>\d+[A-Za-z]?(?:-\d+[A-Za-z]?)?)\s+(?P<name>[A-Za-z][A-Za-z' ]*?)\s+(?P<type>Street|St|Road|Rd|Avenue|Ave|Drive|Dr|Court|Ct|Place|Pl|Crescent|Cres|Lane|Ln|Way|Terrace|Tce|Parade|Pde|Close|Cl|Highway|Hwy|Boulevard|Blvd|Quay|Circuit|Cct)\b", re.I)
_POBOX = re.compile(r"(PO Box|P\.O\. Box|GPO Box|Locked Bag)\s+(\d+)", re.I)


def state_of(text: str, pools: Pools) -> str | None:
    m = re.search(r"\b" + _STATE_RE + r"\b", text)
    if m:
        return pools.state_aliases.get(m.group(1))
    pc = re.search(r"\b(\d{4})\b\s*$", text.strip())
    if pc:
        return pools.postcode_state.get(pc.group(1)[0])
    return None


def suburb(key: bytes, real: str, state: str, pools: Pools, vault) -> tuple[str, str]:
    """Same real suburb -> same surrogate (suburb, postcode) in the same state, everywhere in the matter."""
    kind = f"suburb:{state}"
    n = norm(real)
    got = vault.get(kind, n)
    rows = pools.states.get(state) or pools.states[pools.default_state]
    if got is None:
        names = [r[0] for r in rows]
        got, _ = pick(key, names, "SUBURB", state, n, taken=vault.issued(kind) if len(vault.issued(kind)) < len(names) else None)
        vault.put(kind, n, got)
    pc = dict(rows).get(got, rows[0][1])
    return got, pc


def street(key: bytes, real_name: str, real_type: str | None, pools: Pools, vault) -> str:
    n = norm(real_name + " " + (real_type or ""))
    got = vault.get("street", n)
    if got is None:
        name, _ = pick(key, pools.street_names, "STREET", n)
        typ = real_type if real_type else pools.street_types[hmac_int(key, "STREETTYPE", n) % len(pools.street_types)]
        got = f"{name} {typ}"
        taken = vault.issued("street")
        k = 0
        while got in taken:
            k += 1
            name, _ = pick(key, pools.street_names, "STREET", n, str(k))
            got = f"{name} {typ}"
        vault.put("street", n, got)
    return got


def _case_like(orig: str, new: str) -> str:
    return new.upper() if orig.isupper() and len(orig) > 1 else new


def address(key: bytes, value: str, pools: Pools, vault, attrs: dict | None = None) -> str:
    attrs = attrs or {}
    st = state_of(value, pools) or pools.default_state
    part = attrs.get("address_part")
    n = norm(value)
    if part == "state":
        return value                                      # states are kept
    if part == "postcode" or re.fullmatch(r"\s*\d{4}\s*", value):
        rows = pools.states.get(st) or pools.states[pools.default_state]
        return rows[hmac_int(key, "POSTCODE", n) % len(rows)][1]
    if part == "street_number" or re.fullmatch(r"\s*\d+[A-Za-z]?\s*", value):
        return str(1 + hmac_int(key, "STREETNO", n) % 199)
    if part == "unit":
        return re.sub(r"\d+", lambda m: str(1 + hmac_int(key, "UNIT", n, m.group(0)) % 40), value)
    out = value
    m = _STREET.search(out)
    tail_from = 0
    if m:
        new_street = street(key, m.group("name"), m.group("type"), pools, vault)
        num = str(1 + hmac_int(key, "STREETNO", norm(m.group("num") + m.group("name"))) % 199)
        pre = re.sub(r"\d+", lambda x: str(1 + hmac_int(key, "UNIT", n, x.group(0)) % 40), m.group("pre") or "")
        rep = pre + num + " " + _case_like(m.group("name"), new_street)
        out = out[:m.start()] + rep + out[m.end():]
        tail_from = m.start() + len(rep)
    pbm = _POBOX.search(out)
    if pbm:
        tail_from = max(tail_from, pbm.end())
    loc = _LOCALITY.search(out, tail_from)
    if loc:
        sub, pc = suburb(key, loc.group("suburb").strip(" ,"), st, pools, vault)
        ss, se = loc.start("suburb"), loc.end("suburb")
        lead = len(loc.group("suburb")) - len(loc.group("suburb").lstrip(" ,"))
        out = out[:ss + lead] + _case_like(loc.group("suburb").strip(" ,"), sub) + out[se:]
        loc2 = _LOCALITY.search(out, tail_from)
        if loc2:
            out = out[:loc2.start("postcode")] + pc + out[loc2.end("postcode"):]
    pb = _POBOX.search(out)
    if pb:
        out = out[:pb.start(2)] + str(1000 + hmac_int(key, "POBOX", n) % 8999) + out[pb.end(2):]
    if out == value:   # unparsed: a suburb or a street on its own
        if part == "street" or re.search(r"\b(Street|Road|Avenue|Drive|Court|Place|Lane|Way|Highway|Parade|Terrace)\b", value, re.I):
            out = street(key, value, None, pools, vault)
        else:
            out = _case_like(value, suburb(key, value.strip(), st, pools, vault)[0])
    return out


def location(key: bytes, value: str, granularity: str | None, pools: Pools, vault) -> str:
    st = state_of(value, pools) or pools.default_state
    if granularity == "street":
        return street(key, value, None, pools, vault)
    return _case_like(value, suburb(key, value.strip(), st, pools, vault)[0])


# ---------------------------------------------------------------- organisations (§4.9.4)
def organisation(key: bytes, value: str, pools: Pools, vault) -> str:
    """Synthetic name that keeps the kind (suffix); the same organisation always gets the same name."""
    suffix = ""
    low = value.casefold()
    for s in sorted(pools.org_suffixes, key=len, reverse=True):
        if low.endswith(s.casefold()) and len(value) > len(s):
            suffix = value[len(value) - len(s):]
            break
    core = value[: len(value) - len(suffix)].strip() if suffix else value
    n = norm(core)
    got = vault.get("org", n)
    if got is None:
        from .names import NAME_KINDS
        taken = set().union(*(vault.issued(k) for k in NAME_KINDS))   # injective across name-like kinds
        singles = [x for x in pools.org_stems if x not in taken]
        pool = singles or [f"{a} {b}" for a in pools.org_stems for b in pools.org_stems if a != b and f"{a} {b}" not in taken]
        got, _ = pick(key, pool, "ORG", n)
        vault.put("org", n, got)
    out = f"{got} {suffix}".strip() if suffix else f"{got} Group"
    return out.upper() if value.isupper() and len(value) > 3 else out
