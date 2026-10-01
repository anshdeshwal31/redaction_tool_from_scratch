from __future__ import annotations

import math

import pytest
from hypothesis import given, strategies as st

from redactor.core.canonical import canonical_bytes, canonical_json, read_json, sha256_obj, write_canonical
from redactor.core.coords import PageGeometry, pixel_box_to_points, px_to_pt, pt_to_px
from redactor.core.types import BBox, RawWord, build_page_text


def test_canonical_is_order_independent_and_rounds_floats():
    a = {"b": 1.123456789, "a": [1, 2.0, {"z": -0.0, "y": "é"}]}
    b = {"a": [1, 2.0, {"y": "é", "z": 0.0}], "b": 1.1234567}
    assert canonical_bytes(a) == canonical_bytes(b)
    assert canonical_json(a) == '{"a":[1,2.0,{"y":"é","z":0.0}],"b":1.1235}'
    assert sha256_obj(a) == sha256_obj(b)


def test_canonical_rejects_nan():
    with pytest.raises(ValueError):
        canonical_json({"x": math.nan})


def test_write_canonical_roundtrip(tmp_path):
    p = tmp_path / "x.json"
    h1 = write_canonical(p, {"k": [3, 2, 1], "a": "b"})
    h2 = write_canonical(p, {"a": "b", "k": [3, 2, 1]})
    assert h1 == h2
    assert read_json(p) == {"a": "b", "k": [3, 2, 1]}
    assert p.read_bytes().endswith(b"\n") and b"\r" not in p.read_bytes()


coord = st.floats(min_value=0, max_value=600, allow_nan=False, allow_infinity=False)


@given(x=coord, y=coord, rot=st.sampled_from([0, 90, 180, 270]),
       ox=st.floats(min_value=-50, max_value=50), oy=st.floats(min_value=-50, max_value=50))
def test_user_display_roundtrip(x, y, rot, ox, oy):
    g = PageGeometry(612.0, 792.0, rot, ox, oy)
    X, Y = g.user_to_display(x + ox, y + oy)
    ux, uy = g.display_to_user(X, Y)
    assert ux == pytest.approx(x + ox, abs=1e-6) and uy == pytest.approx(y + oy, abs=1e-6)
    w, h = g.display_size
    assert -1e-6 <= X <= w + 1e-6 or not (0 <= x <= 612 and 0 <= y <= 792)


def test_rotation_semantics():
    g = PageGeometry(600.0, 800.0, 0)
    assert g.user_to_display(0, 800) == (0, 0)          # top-left
    g90 = PageGeometry(600.0, 800.0, 90)
    assert g90.display_size == (800.0, 600.0)
    assert g90.user_to_display(0, 800) == (800, 0)      # top-left goes to top-right (clockwise)
    box = g.user_box_to_display(10, 700, 110, 720)
    assert box == BBox(10, 80, 110, 100)


@given(v=st.floats(min_value=0, max_value=5000, allow_nan=False), dpi=st.sampled_from([72, 150, 200, 300, 400]))
def test_px_pt_roundtrip(v, dpi):
    assert px_to_pt(pt_to_px(v, dpi), dpi) == pytest.approx(v, abs=1e-9)


def test_pixel_box_with_offset():
    b = pixel_box_to_points(300, 600, 150, 30, 300, offset_x_pt=10, offset_y_pt=20)
    assert b == BBox(82.0, 164.0, 118.0, 171.2)


def test_build_page_text_offsets():
    raw = [RawWord("Hello", BBox(0, 0, 10, 5), 90.0, 1, 1), RawWord("world", BBox(12, 0, 22, 5), 91.0, 1, 1),
           RawWord("Next", BBox(0, 10, 8, 15), None, 1, 2), RawWord("  ", BBox(9, 10, 10, 15), None, 1, 2),
           RawWord("line", BBox(9, 10, 16, 15), None, 1, 2)]
    text, words = build_page_text(raw)
    assert text == "Hello world\nNext line"
    assert all(text[w.start:w.end] == w.text for w in words)
    assert [w.line for w in words] == [0, 0, 1, 1]
    assert [w.index for w in words] == [0, 1, 2, 3]
