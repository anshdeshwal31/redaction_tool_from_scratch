"""OCR metrics (plan §4.5): CER/WER after NFKC + whitespace normalisation (case-sensitive and
case-insensitive), order-agnostic bag-of-words F1, entity-surface recovery and mean word confidence."""

from __future__ import annotations

import unicodedata
from collections import Counter
from typing import Any, Iterable, Sequence

from rapidfuzz.distance import Levenshtein

NEAR_CER = 0.2
RECOVERY = ("exact", "near", "degraded", "missing")


def normalize(text: str, *, casefold: bool = False) -> str:
    t = unicodedata.normalize("NFKC", text or "")
    t = " ".join(t.split())
    return t.casefold() if casefold else t


def cer(ref: str, hyp: str, *, casefold: bool = False) -> float | None:
    r, h = normalize(ref, casefold=casefold), normalize(hyp, casefold=casefold)
    if not r:
        return None if not h else 1.0
    return round(Levenshtein.distance(r, h) / len(r), 4)


def wer(ref: str, hyp: str, *, casefold: bool = False) -> float | None:
    r, h = normalize(ref, casefold=casefold).split(), normalize(hyp, casefold=casefold).split()
    if not r:
        return None if not h else 1.0
    return round(Levenshtein.distance(r, h) / len(r), 4)


def bag_of_words_f1(ref: str, hyp: str, *, casefold: bool = True) -> float | None:
    r = Counter(normalize(ref, casefold=casefold).split())
    h = Counter(normalize(hyp, casefold=casefold).split())
    if not r and not h:
        return None
    common = sum((r & h).values())
    p = common / sum(h.values()) if h else 0.0
    rc = common / sum(r.values()) if r else 0.0
    return round(2 * p * rc / (p + rc), 4) if p + rc else 0.0


def surface_recovery(gold_text: str | None, extracted: str | None) -> str:
    """exact | near (CER <= 0.2) | degraded | missing — compared after normalisation, case-sensitive."""
    if extracted is None or not normalize(extracted):
        return "missing"
    if gold_text is None:
        return "degraded"
    g, x = normalize(gold_text), normalize(extracted)
    if g == x:
        return "exact"
    c = cer(g, x)
    return "near" if c is not None and c <= NEAR_CER else "degraded"


def mean_conf(confs: Iterable[float | None]) -> float | None:
    vals = [c for c in confs if c is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


def page_text_metrics(ref: str, hyp: str) -> dict[str, Any]:
    return {"cer": cer(ref, hyp), "cer_ci": cer(ref, hyp, casefold=True), "wer": wer(ref, hyp),
            "wer_ci": wer(ref, hyp, casefold=True), "bow_f1": bag_of_words_f1(ref, hyp)}


def summarize_recovery(items: Sequence[tuple[str, str]]) -> dict[str, Any]:
    """items: (entity_type, recovery class)."""
    by_type: dict[str, Counter] = {}
    total: Counter = Counter()
    for t, r in items:
        by_type.setdefault(t, Counter())[r] += 1
        total[r] += 1
    return {"all": {k: total.get(k, 0) for k in RECOVERY},
            "by_type": {t: {k: c.get(k, 0) for k in RECOVERY} for t, c in sorted(by_type.items())}}
