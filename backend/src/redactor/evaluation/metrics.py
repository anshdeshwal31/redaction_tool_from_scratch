"""Detection and protection metrics (plan §4.5).

Per document, `evaluate_document` turns gold mentions and predictions into small event records; the
aggregators sum events into P/R/F1 with raw counts, per type, group, report bucket, page class and
document, micro and macro. Excluded gold (uncertain, REVIEW, region-only, ignore regions, incomplete
pages) is never a TP or FN, and predictions that only touch excluded gold are neutral, not FP.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

from ..core.canonical import sha256_hex
from ..core.types import BBox, EntitySpan
from ..taxonomy import PROTECT_ACTIONS, Policy, Taxonomy
from .gold import GoldMention
from .matching import SCHEMES, MSpan, covered_chars, match, overlap

PROTECT_MODES = ("pessimistic", "with_review")
NONE = "<none>"


def prf(tp: int, fp: int, fn: int) -> dict[str, Any]:
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    if p is None or r is None:
        f1 = None
    else:
        f1 = 0.0 if p + r == 0 else 2 * p * r / (p + r)
    rd = (lambda x: None if x is None else round(x, 4))
    return {"tp": tp, "fp": fp, "fn": fn, "precision": rd(p), "recall": rd(r), "f1": rd(f1)}


def ratio(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def pred_mspan(s: EntitySpan) -> MSpan:
    key = (s.document_id, -1 if s.page is None else s.page, s.field or "", s.text_source_id)
    ident = f"{s.detector}|{s.rule_id or ''}|{s.start}|{s.end}|{s.entity_type}"
    return MSpan(key, s.start, s.end, s.entity_type, ident)


def gold_mspan(g: GoldMention) -> MSpan:
    return MSpan(g.key, g.start or 0, g.end or 0, g.entity_type, g.mention_id)


def pred_action(s: EntitySpan, policy: Policy, taxonomy: Taxonomy) -> str:
    attrs = {k: v for k, v in s.attributes}
    return policy.decide(s.entity_type, s.attr("role"), attrs, taxonomy.validator(s.entity_type)).action


def is_protect(action: str, mode: str) -> bool:
    return action in PROTECT_ACTIONS or (mode == "with_review" and action == "REVIEW")


@dataclass
class DocEval:
    document_id: str
    split: str
    events: list[tuple] = field(default_factory=list)          # (scheme, outcome, type, page_class)
    type_pairs: list[tuple[str, str]] = field(default_factory=list)
    char_cov: list[tuple[str, int, int]] = field(default_factory=list)   # (type, covered, total)
    gold_protect: list[dict[str, Any]] = field(default_factory=list)
    pred_protect: list[dict[str, Any]] = field(default_factory=list)
    keep_violations: list[dict[str, Any]] = field(default_factory=list)
    mention_status: dict[str, dict[str, Any]] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)


def _in_ignore(s: EntitySpan, ignore: Mapping[int, list[BBox]]) -> bool:
    if s.page is None or s.page not in ignore or not s.bboxes:
        return False
    return all(any(b.overlap_fraction(ig) >= 0.5 for ig in ignore[s.page]) for b in s.bboxes)


def evaluate_document(document_id: str, gold: Sequence[GoldMention], spans: Sequence[EntitySpan], *,
                      policy: Policy, taxonomy: Taxonomy, page_classes: Mapping[int, str],
                      complete_pages: set[int], complete_fields: set[str],
                      ignore_regions: Mapping[int, list[BBox]] | None = None, split: str = "dev") -> DocEval:
    ev = DocEval(document_id, split)
    ignore_regions = ignore_regions or {}
    pc = (lambda page: "field" if page is None else page_classes.get(page, "unknown"))
    # Predictions on pages/fields that are not completely annotated are not scored.
    preds = [s for s in spans if (s.page is not None and s.page in complete_pages) or (s.field is not None and s.field in complete_fields)]
    scored_gold = [g for g in gold if g.excluded is None]
    excluded_gold = [g for g in gold if g.excluded is not None and g.projected]
    gproj = [g for g in scored_gold if g.projected]
    g_ms = [gold_mspan(g) for g in gproj]
    p_ms = [pred_mspan(s) for s in preds]
    ex_ms = [gold_mspan(g) for g in excluded_gold]
    neutral = [any(overlap(pm, em) > 0 for em in ex_ms) or _in_ignore(s, ignore_regions) for s, pm in zip(preds, p_ms)]
    ev.counts = {"gold_scored": len(scored_gold), "gold_not_projected": len(scored_gold) - len(gproj),
                 "gold_excluded": len(gold) - len(scored_gold), "pred_total": len(spans), "pred_scored": len(preds),
                 "pred_neutral": sum(1 for n in neutral if n), "gold_keep": sum(1 for g in gproj if g.action == "KEEP")}
    unprojected = [g for g in scored_gold if not g.projected]
    for scheme in SCHEMES:
        pairs = match(g_ms, p_ms, scheme, taxonomy.compatible_types)
        mg = {i for i, _ in pairs}
        mp = {j for _, j in pairs}
        for i, j in pairs:
            ev.events.append((scheme, "tp", gproj[i].entity_type, pc(gproj[i].page)))
        for i, g in enumerate(gproj):
            if i not in mg:
                ev.events.append((scheme, "fn", g.entity_type, pc(g.page)))
        for g in unprojected:
            ev.events.append((scheme, "fn", g.entity_type, pc(g.page)))
        for j, s in enumerate(preds):
            if j not in mp and not neutral[j]:
                ev.events.append((scheme, "fp", s.entity_type, pc(s.page)))
        if scheme == "overlap_any":
            for i, j in pairs:
                ev.type_pairs.append((gproj[i].entity_type, preds[j].entity_type))
            ev.type_pairs += [(gproj[i].entity_type, NONE) for i in range(len(gproj)) if i not in mg]
            ev.type_pairs += [(g.entity_type, NONE) for g in unprojected]
            ev.type_pairs += [(NONE, s.entity_type) for j, s in enumerate(preds) if j not in mp and not neutral[j]]
            for i, g in enumerate(gproj):
                ev.mention_status.setdefault(g.mention_id, {})["detected"] = i in mg
    for g, gm in zip(gproj, g_ms):
        ev.char_cov.append((g.entity_type, covered_chars(gm, p_ms), gm.length))
    # Protection (action-aware).
    actions = [pred_action(s, policy, taxonomy) for s in preds]
    gp = [(g, gm) for g, gm in zip(gproj, g_ms) if g.protect]
    for mode in PROTECT_MODES:
        prot = [pm for pm, a in zip(p_ms, actions) if is_protect(a, mode)]
        for g in scored_gold:
            if not g.protect:
                continue
            if g.projected:
                gm = gold_mspan(g)
                cov = covered_chars(gm, prot)
                total = gm.length
            else:
                cov, total = 0, max(1, len(g.text or ""))
            ev.gold_protect.append({"mode": mode, "id": g.mention_id, "type": g.entity_type, "page_class": pc(g.page),
                                    "critical": g.critical, "covered": cov, "total": total, "full": cov >= total and total > 0})
            if mode == "pessimistic":
                ev.mention_status.setdefault(g.mention_id, {})["protected"] = cov >= total and total > 0
                ev.mention_status[g.mention_id]["covered_any_action"] = (covered_chars(gold_mspan(g), p_ms) if g.projected else 0) >= total
        gp_ms = [gm for _, gm in gp]
        for j, (s, pm, a) in enumerate(zip(preds, p_ms, actions)):
            if not is_protect(a, mode) or neutral[j]:
                continue
            inside = covered_chars(pm, gp_ms)
            ev.pred_protect.append({"mode": mode, "type": s.entity_type, "page_class": pc(s.page),
                                    "hits_gold": inside > 0, "chars_in": inside, "chars": pm.length})
        for g, gm in zip(gproj, g_ms):
            if g.action == "KEEP" and covered_chars(gm, prot) > 0:
                ev.keep_violations.append({"mode": mode, "type": g.entity_type, "page_class": pc(g.page)})
    return ev


# ---------------------------------------------------------------- aggregation
def _counts(events: Iterable[tuple], scheme: str, keyfn: Callable[[tuple], str] | None = None) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
    for e in events:
        if e[0] != scheme:
            continue
        k = keyfn(e) if keyfn else "all"
        if k is None:
            continue
        out[k][e[1]] += 1
    return out


def _table(counts: Mapping[str, Mapping[str, int]]) -> dict[str, dict[str, Any]]:
    return {k: prf(v["tp"], v["fp"], v["fn"]) for k, v in sorted(counts.items())}


def detection_metrics(evals: Sequence[DocEval], taxonomy: Taxonomy) -> dict[str, Any]:
    events = [(*e, ev.document_id) for ev in evals for e in ev.events]
    out: dict[str, Any] = {}
    for scheme in SCHEMES:
        micro = _counts(events, scheme)["all"]
        by_type = _table(_counts(events, scheme, lambda e: e[2]))
        f1s = [v["f1"] for k, v in by_type.items() if v["tp"] + v["fn"] > 0 and v["f1"] is not None]
        support_types = [k for k, v in by_type.items() if v["tp"] + v["fn"] > 0]
        macro = round(sum(by_type[k]["f1"] or 0.0 for k in support_types) / len(support_types), 4) if support_types else None
        out[scheme] = {
            "micro": prf(micro["tp"], micro["fp"], micro["fn"]),
            "macro_f1": macro, "macro_types": len(support_types), "macro_f1_defined_only": round(sum(f1s) / len(f1s), 4) if f1s else None,
            "by_type": by_type,
            "by_group": _table(_counts(events, scheme, lambda e: taxonomy.group(e[2]))),
            "by_bucket": _table(_counts(events, scheme, lambda e: taxonomy.bucket_of(e[2]))),
            "by_page_class": _table(_counts(events, scheme, lambda e: e[3])),
            "by_document": _table(_counts(events, scheme, lambda e: e[4])),
        }
    pairs = [p for ev in evals for p in ev.type_pairs]
    matched = [(g, p) for g, p in pairs if g != NONE and p != NONE]
    confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for g, p in pairs:
        confusion[g][p] += 1
    out["type_accuracy"] = {"pairs": len(matched), "exact": ratio(sum(1 for g, p in matched if g == p), len(matched)),
                            "compatible": ratio(sum(1 for g, p in matched if taxonomy.compatible_types(g, p)), len(matched))}
    out["confusion"] = {g: dict(sorted(v.items())) for g, v in sorted(confusion.items())}
    cov = [c for ev in evals for c in ev.char_cov]
    by_t: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for t, c, n in cov:
        by_t[t][0] += c
        by_t[t][1] += n
    out["char_coverage"] = {"all": ratio(sum(c for _, c, _ in cov), sum(n for _, _, n in cov)),
                            "by_type": {t: ratio(v[0], v[1]) for t, v in sorted(by_t.items())}}
    return out


def protection_metrics(evals: Sequence[DocEval]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for mode in PROTECT_MODES:
        gp = [g for ev in evals for g in ev.gold_protect if g["mode"] == mode]
        pp = [p for ev in evals for p in ev.pred_protect if p["mode"] == mode]
        kv = [k for ev in evals for k in ev.keep_violations if k["mode"] == mode]
        by_type: dict[str, dict[str, int]] = defaultdict(lambda: {"n": 0, "full": 0, "covered": 0, "total": 0})
        for g in gp:
            d = by_type[g["type"]]
            d["n"] += 1
            d["full"] += int(g["full"])
            d["covered"] += g["covered"]
            d["total"] += g["total"]
        crit = [g for g in gp if g["critical"]]
        out[mode] = {
            "gold_protect": len(gp),
            "mention_recall": ratio(sum(1 for g in gp if g["full"]), len(gp)),
            "char_recall": ratio(sum(g["covered"] for g in gp), sum(g["total"] for g in gp)),
            "pred_protect": len(pp),
            "mention_precision": ratio(sum(1 for p in pp if p["hits_gold"]), len(pp)),
            "char_precision": ratio(sum(p["chars_in"] for p in pp), sum(p["chars"] for p in pp)),
            "keep_mentions": sum(ev.counts.get("gold_keep", 0) for ev in evals),
            "keep_violations": len(kv),
            "keep_violations_by_type": dict(sorted(_count(k["type"] for k in kv).items())),
            "critical": {"total": len(crit), "missed": sum(1 for g in crit if not g["full"])},
            "residual_mentions": sum(1 for g in gp if not g["full"]),
            "by_type": {t: {"mentions": d["n"], "mention_recall": ratio(d["full"], d["n"]), "char_recall": ratio(d["covered"], d["total"])}
                        for t, d in sorted(by_type.items())},
        }
    return out


def _count(items: Iterable[str]) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for i in items:
        out[i] += 1
    return out


# ---------------------------------------------------------------- small-sample honesty
def bootstrap_ci(evals: Sequence[DocEval], stat: Callable[[Sequence[DocEval]], float | None], *,
                 n: int = 1000, seed: str = "bootstrap-v1", alpha: float = 0.05) -> dict[str, Any]:
    """Document-level bootstrap; resampling indices come from SHA-256 of (seed, round, slot)."""
    docs = sorted(evals, key=lambda e: e.document_id)
    k = len(docs)
    if k == 0:
        return {"n_docs": 0, "lo": None, "hi": None}
    vals = []
    for b in range(n):
        sample = [docs[int(sha256_hex(f"{seed}|{b}|{s}")[:12], 16) % k] for s in range(k)]
        v = stat(sample)
        if v is not None:
            vals.append(v)
    if not vals:
        return {"n_docs": k, "lo": None, "hi": None}
    vals.sort()
    lo = vals[int((alpha / 2) * (len(vals) - 1))]
    hi = vals[int((1 - alpha / 2) * (len(vals) - 1))]
    return {"n_docs": k, "rounds": n, "lo": round(lo, 4), "hi": round(hi, 4)}


def stat_f1(scheme: str = "overlap_any") -> Callable[[Sequence[DocEval]], float | None]:
    def f(sample: Sequence[DocEval]) -> float | None:
        tp = fp = fn = 0
        for ev in sample:
            for e in ev.events:
                if e[0] == scheme:
                    tp += e[1] == "tp"
                    fp += e[1] == "fp"
                    fn += e[1] == "fn"
        return prf(tp, fp, fn)["f1"]
    return f


def stat_protection_recall(mode: str = "pessimistic") -> Callable[[Sequence[DocEval]], float | None]:
    def f(sample: Sequence[DocEval]) -> float | None:
        gp = [g for ev in sample for g in ev.gold_protect if g["mode"] == mode]
        return ratio(sum(1 for g in gp if g["full"]), len(gp))
    return f
