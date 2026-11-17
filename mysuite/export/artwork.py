"""The artwork's own size, and the exact pixel size rsvg-convert will give a raster output.

Used for the {width}/{height} naming tokens (known before anything is rendered) and for vector output at the
artwork's own size. Sizes are in points at 72 dpi, which is what rsvg-convert makes of them when it writes a PDF
at --dpi 72: a 333 px wide SVG becomes a 333 pt wide page, a 100 mm wide one stays 100 mm.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

from mysuite.export.units import Margin, Size

# points per unit (1 px = 1 pt at 72 dpi, the convention design tools use when they write SVG)
_PT_PER_UNIT = {"": 1.0, "px": 1.0, "pt": 1.0, "pc": 12.0, "in": 72.0, "cm": 72 / 2.54, "mm": 72 / 25.4}
_LENGTH_RE = re.compile(r"^\s*([0-9]*\.?[0-9]+(?:[eE][-+]?\d+)?)\s*(px|pt|pc|in|cm|mm)?\s*$")


def _attr(tag: str, name: str) -> str | None:
    match = re.search(rf'\s{name}\s*=\s*(["\'])(.*?)\1', tag, re.S)
    return match.group(2) if match else None


def _length_pt(value: str | None) -> float | None:
    if value is None:
        return None
    match = _LENGTH_RE.match(value)
    if not match:
        return None                                       # "100%", "auto", garbage: not an absolute size
    number = float(match.group(1))
    return number * _PT_PER_UNIT[(match.group(2) or "").lower()] if number > 0 else None


def root_tag(text: str) -> str | None:
    match = re.search(r"<svg\b[^>]*>", text, re.S)
    return match.group(0) if match else None


def viewbox(tag: str) -> tuple[float, float, float, float] | None:
    raw = _attr(tag, "viewBox")
    if not raw:
        return None
    try:
        parts = [float(p) for p in raw.replace(",", " ").split()]
    except ValueError:
        return None
    return (parts[0], parts[1], parts[2], parts[3]) if len(parts) == 4 and parts[2] > 0 and parts[3] > 0 else None


def artwork_size_text(text: str) -> tuple[float, float] | None:
    """(width, height) in pt of an SVG document, as rsvg-convert sizes it; None when it can't be told."""
    tag = root_tag(text)
    if tag is None:
        return None
    width, height = _length_pt(_attr(tag, "width")), _length_pt(_attr(tag, "height"))
    box = viewbox(tag)
    if width and height:
        return width, height
    if box:
        aspect = box[2] / box[3]
        if width:
            return width, width / aspect
        if height:
            return height * aspect, height
        return box[2], box[3]
    return None


def artwork_size(svg_path: Path) -> tuple[float, float] | None:
    try:
        text = svg_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return artwork_size_text(text)


def _up(value: float) -> int:
    return max(1, math.ceil(value - 1e-6))        # rsvg-convert rounds the scaled side up


def raster_dims(artwork: tuple[float, float], size: Size, margin: Margin | None = None) -> tuple[int, int]:
    """The pixel size of a raster output at `size` (plus its margin): what {width}/{height} name."""
    w, h = artwork
    px = size.pixels
    if size.fit == "height":
        dims = (_up(px * w / h), px)
    elif size.fit == "width":
        dims = (px, _up(px * h / w))
    elif w >= h:
        dims = (px, _up(px * h / w))
    else:
        dims = (_up(px * w / h), px)
    if margin is not None:
        dims = (dims[0] + margin.left + margin.right, dims[1] + margin.top + margin.bottom)
    return dims


def size_pt(size: Size) -> float:
    """A chosen vector size in pt: 512 (px) is 512 pt, 100mm is 283.46 pt (vectors are written at 72 dpi)."""
    match = re.match(r"^(\d+(?:\.\d+)?)(mm|cm|in)$", size.label.strip().lower())
    return float(match.group(1)) * 72.0 / {"mm": 25.4, "cm": 2.54, "in": 1.0}[match.group(2)] if match else float(size.pixels)


def vector_dims(artwork: tuple[float, float], size: Size) -> tuple[float, float]:
    """The page size in pt of a vector output: the artwork's own, or scaled so `size` fits like a raster would."""
    w, h = artwork
    if size.is_artwork:
        return w, h
    target = size_pt(size)
    if size.fit == "height" or (size.fit == "longest" and h > w):
        return target * w / h, target
    return target, target * h / w
