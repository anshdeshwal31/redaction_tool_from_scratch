"""Inter-annotator agreement (plan §9.3), built on the detection matching code (§4.5).

Two annotation sets of the same document are projected onto the same text sources, then compared:
span F1 (strict, exact-boundary, overlap-typed, overlap-any; per type group, both directions),
token-level Cohen's kappa (protect vs keep vs none; type on overlap-matched pairs), B-cubed F1 of the
canonical grouping, role and gender agreement, action agreement, region IoU and page-class agreement.
`iaa.public.json` holds metrics only; the disagreement list (mention IDs and categories) is private.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Mapping, Sequence

from ..core.types import BBox, DocumentText
from ..dataset.models import AnnotationSet
from ..taxonomy import PROTECT_ACTIONS, Policy, Taxonomy
from .gold import GoldDocument, GoldMention, project_document
from .matching import SCHEMES, MSpan, match
from .metrics import prf, ratio

TARGETS = {"strict_f1_protect": 0.90, "kappa_protect": 0.85}


def cohen_kappa(a: Sequence[str], b: Sequence[str]) -> float | None:
    n = len(a)
    if n == 0 or n != len(b):
        return None
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[k] * cb.get(k, 0) for k in ca) / (n * n)
    if pe >= 1.0:
        return 1.0 if po == 1.0 else None
    return round((po - pe) / (1 - pe), 4)


def b_cubed(items: Sequence[tuple[str, str]]) -> dict[str, Any]:
    """items: (cluster in A, cluster in B) per matched mention. Returns B-cubed P/R/F1."""
    n = len(items)
    if n == 0:
        return {"precision": None, "recall": None, "f1": None, "n": 0}
    by_a: dict[str, list[int]] = defaultdict(list)
    by_b: dict[str, list[int]] = defaultdict(list)
    for i, (ca, cb) in enumerate(items):
        by_a[ca].append(i)
        by_b[cb].append(i)
    p = r = 0.0
    for i, (ca, cb) in enumerate(items):
        sa, sb = set(by_a[ca]), set(by_b[cb])
        inter = len(sa & sb)
        p += inter / len(sb)
        r += inter / len(sa)
    p, r = p / n, r / n
    f = 2 * p * r / (p + r) if p + r else 0.0
    return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f, 4), "n": n}


def _ms(m: GoldMention) -> MSpan:
    return MSpan(m.key, m.start or 0, m.end or 0, m.entity_type, m.mention_id)


def _cluster(m: GoldMention) -> str:
    return m.canonical_id or f"singleton:{m.mention_id}"


def _union_box(ann: AnnotationSet, mention_id: str) -> BBox | None:
    for e in ann.entities:
        if e.entity_id == mention_id and e.regions:
            return BBox.union_all(BBox(r.x0, r.y0, r.x1, r.y1) for r in e.regions)
    return None


def compare(doc: DocumentText, a: GoldDocument, b: GoldDocument, *, policy: Policy, taxonomy: Taxonomy,
            page_classes: Mapping[int, str] | None = None, genders_a: Mapping[str, str] | None = None,
            genders_b: Mapping[str, str] | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Returns (public metrics, private disagreement list) for one document."""
    pages = sorted(a.pages_complete & b.pages_complete)
    ma = [m for m in project_document(a, doc, policy=policy, taxonomy=taxonomy, page_classes=page_classes)
          if m.projected and m.page in pages and not taxonomy.region_only(m.entity_type)]
    mb = [m for m in project_document(b, doc, policy=policy, taxonomy=taxonomy, page_classes=page_classes)
          if m.projected and m.page in pages and not taxonomy.region_only(m.entity_type)]
    sa, sb = [_ms(m) for m in ma], [_ms(m) for m in mb]
    public: dict[str, Any] = {"document_id": doc.document_id, "pages_compared": len(pages),
                              "mentions_a": len(ma), "mentions_b": len(mb), "span": {}}
    for scheme in SCHEMES:
        pairs = match(sa, sb, scheme, taxonomy.compatible_types)
        tp = len(pairs)
        both = {"a_as_gold": prf(tp, len(sb) - tp, len(sa) - tp), "b_as_gold": prf(tp, len(sa) - tp, len(sb) - tp)}
        groups: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
        mi = {i for i, _ in pairs}
        mj = {j for _, j in pairs}
        for i, _ in pairs:
            groups[taxonomy.group(ma[i].entity_type)][0] += 1
        for j, m in enumerate(mb):
            if j not in mj:
                groups[taxonomy.group(m.entity_type)][1] += 1
        for i, m in enumerate(ma):
            if i not in mi:
                groups[taxonomy.group(m.entity_type)][2] += 1
        both["by_group"] = {g: prf(*v) for g, v in sorted(groups.items())}
        prot_pairs = [(i, j) for i, j in pairs if ma[i].protect]
        pa, pb = sum(1 for m in ma if m.protect), sum(1 for m in mb if m.protect)
        both["protect_types"] = prf(len(prot_pairs), pb - sum(1 for _, j in prot_pairs), pa - len(prot_pairs))
        public["span"][scheme] = both
    pairs = match(sa, sb, "overlap_any", taxonomy.compatible_types)
    # token-level kappa over the words of the compared pages
    lab_a, lab_b = [], []
    for page in pages:
        pe = doc.pages.get(page)
        if pe is None:
            continue
        for w in pe.words:
            lab_a.append(_token_label(w.start, w.end, [m for m in ma if m.page == page]))
            lab_b.append(_token_label(w.start, w.end, [m for m in mb if m.page == page]))
    public["kappa"] = {
        "protect_keep_none": cohen_kappa(lab_a, lab_b),
        "protect_binary": cohen_kappa(["P" if x == "protect" else "N" for x in lab_a], ["P" if x == "protect" else "N" for x in lab_b]),
        "type_on_matched": cohen_kappa([ma[i].entity_type for i, _ in pairs], [mb[j].entity_type for _, j in pairs]),
        "tokens": len(lab_a),
    }
    public["canonical_b3"] = b_cubed([(_cluster(ma[i]), _cluster(mb[j])) for i, j in pairs if ma[i].entity_type in ("PERSON", "ORGANIZATION")])
    role_pairs = [(ma[i].role, mb[j].role) for i, j in pairs if ma[i].role or mb[j].role]
    public["role_agreement"] = ratio(sum(1 for x, y in role_pairs if x == y), len(role_pairs))
    if genders_a is not None and genders_b is not None:
        gp = [(genders_a.get(ma[i].canonical_id or ""), genders_b.get(mb[j].canonical_id or "")) for i, j in pairs
              if ma[i].entity_type == "PERSON"]
        public["gender_agreement"] = ratio(sum(1 for x, y in gp if x == y), len(gp))
    public["action_agreement"] = ratio(sum(1 for i, j in pairs if ma[i].action == mb[j].action), len(pairs))
    ious = []
    for i, j in pairs:
        ba, bb = _union_box(a.annotation, ma[i].mention_id), _union_box(b.annotation, mb[j].mention_id)
        if ba is not None and bb is not None:
            ious.append(ba.iou(bb))
    public["region_iou_mean"] = round(sum(ious) / len(ious), 4) if ious else None
    public["targets"] = TARGETS
    private = disagreements(ma, mb, pairs)
    public["disagreements"] = dict(sorted(Counter(d["category"] for d in private).items()))
    return public, private


def _token_label(start: int, end: int, mentions: Sequence[GoldMention]) -> str:
    for m in mentions:
        if m.start is not None and m.start < end and start < (m.end or 0):
            return "protect" if m.action in PROTECT_ACTIONS else "keep"
    return "none"


def disagreements(ma: Sequence[GoldMention], mb: Sequence[GoldMention], pairs: Sequence[tuple[int, int]]) -> list[dict[str, Any]]:
    out = []
    mi = {i for i, _ in pairs}
    mj = {j for _, j in pairs}
    for i, j in pairs:
        a, b = ma[i], mb[j]
        if (a.start, a.end) != (b.start, b.end):
            out.append({"category": "boundary", "a": a.mention_id, "b": b.mention_id, "page": a.page})
        if a.entity_type != b.entity_type:
            out.append({"category": "type", "a": a.mention_id, "b": b.mention_id, "page": a.page})
        if (a.role or None) != (b.role or None):
            out.append({"category": "role", "a": a.mention_id, "b": b.mention_id, "page": a.page})
        if a.entity_type == "PERSON" and bool(a.canonical_id) != bool(b.canonical_id):
            out.append({"category": "canonical", "a": a.mention_id, "b": b.mention_id, "page": a.page})
    out += [{"category": "missed", "a": m.mention_id, "b": None, "page": m.page} for i, m in enumerate(ma) if i not in mi]
    out += [{"category": "extra", "a": None, "b": m.mention_id, "page": m.page} for j, m in enumerate(mb) if j not in mj]
    return sorted(out, key=lambda d: (d["page"] or 0, d["category"], d["a"] or "", d["b"] or ""))


def page_class_agreement(a: Mapping[int, str], b: Mapping[int, str]) -> dict[str, Any]:
    pages = sorted(set(a) & set(b))
    return {"pages": len(pages), "agreement": ratio(sum(1 for p in pages if a[p] == b[p]), len(pages)),
            "kappa": cohen_kappa([a[p] for p in pages], [b[p] for p in pages])}
