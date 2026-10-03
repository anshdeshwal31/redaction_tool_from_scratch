"""Writes the two Philter policies (plan §11 C1) deterministically: `default` (every built-in filter that
works offline; the `person` filter needs the separate PhEye model service, which is not part of the
pinned image) and `au_extended` (default plus Australian identifier patterns). No PII.

Run: python sidecars/philter/make_policies.py
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
REDACT = [{"strategy": "REDACT", "redactionFormat": "{{{REDACTED-%t}}}"}]

BUILTIN = ["age", "bankRoutingNumber", "bitcoinAddress", "creditCard", "currency", "date", "driversLicense", "emailAddress",
           "ibanCode", "ipAddress", "macAddress", "passportNumber", "phoneNumber", "phoneNumberExtension", "ssn",
           "streetAddress", "trackingNumber", "url", "vin", "zipCode", "city", "county", "state", "stateAbbreviation",
           "hospital", "firstName", "surname", "physicianName"]

# Australian formats (public specifications; no matter data). Checksums are not applied by Philter; the
# evaluation scores what Philter returns.
AU = [
    ("au-medicare", r"\b[2-6]\d{3}\s?\d{5}\s?\d(?:\s?/?\s?\d)?\b"),
    ("au-tfn", r"\b\d{3}\s?\d{3}\s?\d{2,3}\b"),
    ("au-abn", r"\b\d{2}\s?\d{3}\s?\d{3}\s?\d{3}\b"),
    ("au-acn", r"\b\d{3}\s?\d{3}\s?\d{3}\b"),
    ("au-phone", r"(?:\+61\s?|\b0)[2-478](?:[\s-]?\d){8}\b"),
    ("au-phone-13", r"\b1[38]00(?:[\s-]?\d){6}\b"),
    ("au-postcode-state", r"\b(?:NSW|VIC|QLD|SA|WA|TAS|NT|ACT)\s+\d{4}\b"),
    ("au-provider-number", r"\b\d{6}[0-9A-Z][A-HJ-NP-Y]\b"),
    ("au-ahpra", r"\b[A-Z]{3}\d{10}\b"),
]


def policy(name: str, au: bool) -> dict:
    ids = {k: {f"{k}FilterStrategies": REDACT} for k in BUILTIN}
    if au:
        ids["identifiers"] = [{"classification": c, "pattern": p, "caseSensitive": True, "identifierFilterStrategies": REDACT}
                              for c, p in AU]
    return {"name": name, "identifiers": ids}


def main() -> None:
    out = HERE / "policies"
    out.mkdir(exist_ok=True)
    for name, au in (("default", False), ("au_extended", True)):
        (out / f"{name}.json").write_text(json.dumps(policy(name, au), indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
