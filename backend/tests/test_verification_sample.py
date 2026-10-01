"""Verification sample (plan §9.4) on synthetic page lists: sizes, stratification, determinism."""

from redactor.dataset.sample import allocate, draw, sample_size, stratified_pages


def test_sample_size_rule():
    assert sample_size(4, 0.2, 5) == 4      # fewer than the minimum: all pages
    assert sample_size(10, 0.2, 5) == 5     # minimum wins
    assert sample_size(30, 0.2, 5) == 6     # 20 %
    assert sample_size(31, 0.2, 5) == 7     # rounded up


def test_allocation_is_proportional_and_exact():
    alloc = allocate({"scanned": list(range(1, 21)), "born_digital": list(range(21, 31))}, 6)
    assert alloc == {"born_digital": 2, "scanned": 4}
    assert sum(allocate({"a": [1], "b": [2], "c": [3]}, 2).values()) == 2


def test_stratified_pages_deterministic_and_seed_dependent():
    classes = {p: ("scanned" if p <= 20 else "born_digital") for p in range(1, 31)}
    a = stratified_pages("doc_x", classes, "seed-1", 0.2, 5)
    assert a == stratified_pages("doc_x", dict(reversed(list(classes.items()))), "seed-1", 0.2, 5)
    assert len(a) == 6 and a == sorted(a)
    assert sum(1 for p in a if p > 20) == 2


def test_draw_rules():
    docs = {"t1": {1: "scanned", 2: "scanned"}, "i1": {1: "born_digital"}, "t2": {1: "scanned"},
            "d1": {p: "scanned" for p in range(1, 31)}}
    sample, summary = draw(docs, test_split=["t1", "t2"], iaa=["i1", "t2"], seed="s", rate=0.2, min_pages=5)
    assert "t2" not in sample.pages and summary["t2"]["rule"] == "iaa_from_scratch"
    assert sample.pages["t1"] == [1, 2]
    assert "i1" not in sample.pages and summary["i1"]["rule"] == "iaa_from_scratch"
    assert len(sample.pages["d1"]) == 6
    assert sample.drawn_before_verification
