"""Coordinate transforms (plan §3.1 principle 3).

Canonical space: displayed page space (after /Rotate), PDF points, origin top-left, y down.

pdfium reports char and object boxes in PDF user space (origin bottom-left of the page box,
y up, before /Rotate). Rendered bitmaps and OCR boxes are in displayed pixels.
"""

from __future__ import annotations

from dataclasses import dataclass

from .types import BBox


@dataclass(frozen=True, slots=True)
class PageGeometry:
    """Unrotated page box (user space) plus the page's /Rotate (clockwise degrees)."""
    width: float   # unrotated page-box width in points
    height: float  # unrotated page-box height in points
    rotation: int = 0
    origin_x: float = 0.0  # lower-left of the page box in user space
    origin_y: float = 0.0

    def __post_init__(self) -> None:
        if self.rotation % 90 != 0:
            raise ValueError("rotation must be a multiple of 90")
        object.__setattr__(self, "rotation", self.rotation % 360)

    @property
    def display_size(self) -> tuple[float, float]:
        if self.rotation in (90, 270):
            return self.height, self.width
        return self.width, self.height

    def user_to_display(self, x: float, y: float) -> tuple[float, float]:
        ux, uy = x - self.origin_x, y - self.origin_y
        w, h = self.width, self.height
        r = self.rotation
        if r == 0:
            return ux, h - uy
        if r == 90:
            return uy, ux
        if r == 180:
            return w - ux, uy
        return h - uy, w - ux  # 270

    def display_to_user(self, X: float, Y: float) -> tuple[float, float]:
        w, h = self.width, self.height
        r = self.rotation
        if r == 0:
            ux, uy = X, h - Y
        elif r == 90:
            ux, uy = Y, X
        elif r == 180:
            ux, uy = w - X, Y
        else:  # 270
            ux, uy = w - Y, h - X
        return ux + self.origin_x, uy + self.origin_y

    def user_box_to_display(self, left: float, bottom: float, right: float, top: float) -> BBox:
        corners = [self.user_to_display(x, y) for x in (left, right) for y in (bottom, top)]
        xs = [c[0] for c in corners]
        ys = [c[1] for c in corners]
        return BBox(min(xs), min(ys), max(xs), max(ys))


def px_to_pt(value: float, dpi: float) -> float:
    return value * 72.0 / dpi


def pt_to_px(value: float, dpi: float) -> float:
    return value * dpi / 72.0


def pixel_box_to_points(left: float, top: float, width: float, height: float, dpi: float,
                        offset_x_pt: float = 0.0, offset_y_pt: float = 0.0) -> BBox:
    """OCR pixel box (displayed space) to points; offsets place a crop back on the page."""
    x0 = px_to_pt(left, dpi) + offset_x_pt
    y0 = px_to_pt(top, dpi) + offset_y_pt
    return BBox(x0, y0, x0 + px_to_pt(width, dpi), y0 + px_to_pt(height, dpi))


def points_box_to_pixels(box: BBox, dpi: float) -> tuple[int, int, int, int]:
    """Points to an integer pixel box (left, top, right, bottom), expanded outward."""
    import math
    return (math.floor(pt_to_px(box.x0, dpi)), math.floor(pt_to_px(box.y0, dpi)),
            math.ceil(pt_to_px(box.x1, dpi)), math.ceil(pt_to_px(box.y1, dpi)))


def unrotate_box(b: BBox, rot: int, page_w: float, page_h: float) -> BBox:
    """Map a box from an image turned clockwise by `rot` (90 or 270) back to displayed page space."""
    if rot == 90:   # rotated (x', y') = (H - y, x)
        return BBox(b.y0, page_h - b.x1, b.y1, page_h - b.x0)
    if rot == 270:  # rotated (x', y') = (y, W - x)
        return BBox(page_w - b.y1, b.x0, page_w - b.y0, b.x1)
    if rot == 0:
        return b
    raise ValueError("unsupported rotation")
