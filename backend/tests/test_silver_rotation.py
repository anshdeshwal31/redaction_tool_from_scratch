"""Rotated aux views map boxes back to displayed page space exactly (synthetic geometry only)."""

from hypothesis import given
from hypothesis import strategies as st

from redactor.core.types import BBox
from redactor.dataset.silver import _unrotate_box

W, H = 595.2, 841.68


def _rotate(b: BBox, rot: int) -> BBox:
    """Forward map: page space -> image turned clockwise by `rot` degrees."""
    if rot == 90:
        return BBox(H - b.y1, b.x0, H - b.y0, b.x1)
    return BBox(b.y0, W - b.x1, b.y1, W - b.x0)


coord = st.floats(min_value=0, max_value=500, allow_nan=False, allow_infinity=False)


@given(coord, coord, st.floats(min_value=1, max_value=90), st.floats(min_value=1, max_value=90), st.sampled_from([90, 270]))
def test_round_trip(x0, y0, w, h, rot):
    b = BBox(x0, y0, x0 + w, y0 + h)
    back = _unrotate_box(_rotate(b, rot), rot, W, H)
    for a, c in zip((back.x0, back.y0, back.x1, back.y1), (b.x0, b.y0, b.x1, b.y1)):
        assert abs(a - c) < 1e-6


def test_top_left_of_upright_image_is_bottom_left_of_page_for_cw90():
    # Content that reads bottom-to-top: the upright image's top-left corner is the page's bottom-left.
    b = _unrotate_box(BBox(0, 0, 10, 5), 90, W, H)
    assert (b.x0, b.y1) == (0, H)
