"""Colour-aware SVG rewriting (replaces the old regex-on-hex approach).

Colour literals are located only where SVG puts colours (presentation attributes,
style="" declarations, <style> blocks), parsed to real colours, and compared by
value, so #d00, #dd0000, rgb(221,0,0), hsl(0,100%,43.3%) and a near-identical
#dc0100 are all recognised as the same brand red. The document text is otherwise
left byte-for-byte alone.

A colour written with an alpha component (#dd0000ff, rgba(...)) only matches a FROM
that also states an alpha: it is a different paint from the opaque one.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Callable

from mysuite.color.lab import delta_e2000, rgb_to_lab
from mysuite.color.parse import Color, parse_color

DEFAULT_TOLERANCE = 2.0  # CIEDE2000; ~ the smallest difference a person notices

_PROPS = r"fill|stroke|stop-color|flood-color|lighting-color|color"
_ATTR_RE = re.compile(rf"(?<![\w:-])({_PROPS})(\s*=\s*)([\"'])(.*?)\3", re.DOTALL)
_STYLE_ATTR_RE = re.compile(r"(?<![\w:-])(style\s*=\s*)([\"'])(.*?)\2", re.DOTALL)
_STYLE_BLOCK_RE = re.compile(r"(<style\b[^>]*>)(.*?)(</style>)", re.DOTALL | re.IGNORECASE)
_DECL_RE = re.compile(rf"(?<![\w-])({_PROPS})(\s*:\s*)([^;}}\"'<]+)")
_TOKEN_RE = re.compile(
    r"(?<![\w#-])(?:url\([^)]*\)|#[0-9a-fA-F]{3,8}(?![0-9a-zA-Z])|(?:rgba?|hsla?)\([^)]*\)|[a-zA-Z]+)"
)


@dataclass(frozen=True)
class _Source:
    color: Color
    lab: tuple[float, float, float]
    target: str


def _build(mapping: dict[str, str]) -> list[_Source]:
    sources = []
    for from_text, to_text in mapping.items():
        color = parse_color(from_text)
        if color is None:
            raise ValueError(f"not a colour: {from_text!r}")
        sources.append(_Source(color, rgb_to_lab(color.r, color.g, color.b), to_text))
    return sources


def _find(color: Color, sources: list[_Source], tolerance: float) -> _Source | None:
    lab = rgb_to_lab(color.r, color.g, color.b)
    best: tuple[float, _Source] | None = None
    for source in sources:
        if (source.color.alpha is None) != (color.alpha is None):
            continue
        if color.alpha is not None and abs(source.color.alpha - color.alpha) > 0.005:
            continue
        distance = 0.0 if (source.color[:3] == color[:3]) else delta_e2000(lab, source.lab)
        if distance <= tolerance and (best is None or distance < best[0]):
            best = (distance, source)
    return best[1] if best else None


def _map_value(value: str, fn: Callable[[str, Color], str | None]) -> str:
    def repl(m: re.Match[str]) -> str:
        token = m.group(0)
        color = parse_color(token)
        if color is None:
            return token
        replacement = fn(token, color)
        return token if replacement is None else replacement

    return _TOKEN_RE.sub(repl, value)


def _walk(text: str, fn: Callable[[str, Color], str | None]) -> str:
    """Applies fn(token, colour) to every colour literal in a colour context."""
    def decls(content: str) -> str:
        return _DECL_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{_map_value(m.group(3), fn)}", content)

    text = _ATTR_RE.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{_map_value(m.group(4), fn)}{m.group(3)}", text
    )
    text = _STYLE_ATTR_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{decls(m.group(3))}{m.group(2)}", text)
    return _STYLE_BLOCK_RE.sub(lambda m: f"{m.group(1)}{decls(m.group(2))}{m.group(3)}", text)


def recolor_text(text: str, mapping: dict[str, str], tolerance: float = DEFAULT_TOLERANCE) -> str:
    """Rewrites every colour that equals (or is within `tolerance` CIEDE2000 of) a FROM key."""
    if not mapping:
        return text
    sources = _build(mapping)

    def fn(_token: str, color: Color) -> str | None:
        hit = _find(color, sources, tolerance)
        return hit.target if hit else None

    return _walk(text, fn)


def palette(text: str) -> list[tuple[str, int]]:
    """Distinct colours used (opaque hex), most used first. Alpha is reported in the hex (#rrggbbaa)."""
    counts: Counter[str] = Counter()

    def fn(_token: str, color: Color) -> None:
        if color.alpha is None or color.alpha >= 1:
            counts[color.hex()] += 1
        else:
            counts[color.hex() + f"{round(color.alpha * 255):02x}"] += 1
        return None

    _walk(text, fn)
    return counts.most_common()
