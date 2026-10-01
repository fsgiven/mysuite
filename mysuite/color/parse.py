from __future__ import annotations

import colorsys
import re
from typing import NamedTuple

from PIL import ImageColor


class Color(NamedTuple):
    r: int
    g: int
    b: int
    alpha: float | None  # None = no alpha written; 0..1 otherwise

    def hex(self) -> str:
        return f"#{self.r:02x}{self.g:02x}{self.b:02x}"


_SPECIAL = {"none", "transparent", "currentcolor", "inherit", "initial", "unset", "context-fill", "context-stroke"}
_FUNC_RE = re.compile(r"^(rgba?|hsla?)\(\s*(.*?)\s*\)$", re.DOTALL)


def _number(raw: str, scale: float) -> float:
    """"50%" -> 0.5*scale ; "128" -> 128."""
    raw = raw.strip()
    return float(raw[:-1]) / 100 * scale if raw.endswith("%") else float(raw)


def _function(kind: str, body: str) -> Color | None:
    parts = [p for p in re.split(r"[\s,/]+", body) if p]
    if len(parts) not in (3, 4):
        return None
    try:
        alpha = _number(parts[3], 1.0) if len(parts) == 4 else None
        if kind.startswith("rgb"):
            r, g, b = (min(max(_number(p, 255.0), 0), 255) for p in parts[:3])
        else:
            hue = float(parts[0].removesuffix("deg")) % 360 / 360
            s, l = (min(max(_number(p, 1.0) if p.endswith("%") else float(p) / 100, 0), 1) for p in parts[1:3])
            r, g, b = (c * 255 for c in colorsys.hls_to_rgb(hue, l, s))
    except ValueError:
        return None
    return Color(round(r), round(g), round(b), None if alpha is None else round(min(max(alpha, 0), 1), 4))


def parse_color(text: str) -> Color | None:
    """Parses a CSS/SVG colour literal (hex3/4/6/8, rgb()/rgba() incl. percentages and
    the space/slash syntax, hsl()/hsla(), named). Returns None for anything else (url(),
    none, currentColor, transparent, ...): those are not a fixed colour and must never
    be rewritten."""
    value = text.strip().lower()
    if not value or value in _SPECIAL or value.startswith("url("):
        return None
    m = _FUNC_RE.match(value)
    if m:
        return _function(m.group(1), m.group(2))
    try:
        rgba = ImageColor.getrgb(value)
    except ValueError:
        return None
    if len(rgba) == 4:
        return Color(rgba[0], rgba[1], rgba[2], round(rgba[3] / 255, 4))
    return Color(rgba[0], rgba[1], rgba[2], None)
