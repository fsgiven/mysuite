"""Logo colour variants: negative / on-dark (token-driven) and invert, mono-black, mono-white, grayscale (algorithmic)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from mysuite.color.parse import Color, parse_color
from mysuite.color.svg import map_colours, recolor_text

ALGORITHMIC = ("invert", "mono-black", "mono-white", "grayscale")
TOKEN_DRIVEN = ("negative", "on-dark")
SHAPE = ("knockout", "cutout")
ALL = ("default", *ALGORITHMIC, *TOKEN_DRIVEN, *SHAPE)

_ROOT = re.compile(r"<svg\b[^>]*>", re.I | re.S)


class VariantError(ValueError):
    pass


@dataclass(frozen=True)
class VariantSpec:
    """One output variant: `label` names its folder, `steps` are applied in order (empty = the logo as it is).

    Written as ``[LABEL=]STEP[+STEP...]``, e.g. ``mono-white``, ``knockout+mono:#1D1D1B`` or
    ``negativ_dark=knockout+mono:#1D1D1B``. A step is a named variant, knockout, cutout or mono:COLOUR."""
    label: str
    steps: list[str]

    @property
    def needs_tokens(self) -> bool:
        return any(s in TOKEN_DRIVEN for s in self.steps)

    @property
    def is_default(self) -> bool:
        return not self.steps


_LABEL_RE = re.compile(r"^[\w.-]+$")


def _step(raw: str) -> str:
    step = raw.strip().lower()
    if step.startswith("mono:"):
        colour = parse_color(raw.strip()[5:])
        if colour is None:
            raise VariantError(f"mono:{raw.strip()[5:]} - not a colour (use hex like mono:#1D1D1B or a name like mono:white)")
        return f"mono:{colour.hex()}"
    if step not in ALL or step == "default":
        raise VariantError(f"variants must be from {', '.join(ALL)} or mono:COLOUR, combined with + "
                           f"(unknown: {raw.strip()!r})")
    return step


def parse_spec(text: str) -> VariantSpec:
    raw = text.strip()
    label, sep, body = raw.partition("=")
    if not sep:
        label, body = "", raw
    label = label.strip()
    if sep and not label:
        raise VariantError(f"variant {raw!r}: the name before '=' is empty")
    if label and not _LABEL_RE.match(label):
        raise VariantError(f"variant name {label!r} may only use letters, digits, _ . and -")
    if body.strip().lower() == "default":
        return VariantSpec(label or "default", [])
    parts = body.split("+")
    if any(not p.strip() for p in parts):
        raise VariantError(f"variant {raw!r}: empty step (use knockout+mono:#fff, not knockout+)")
    steps = [_step(p) for p in parts]
    if len(set(steps)) != len(steps):
        raise VariantError(f"variant {raw!r} repeats a step")
    if "knockout" in steps and "cutout" in steps:
        raise VariantError(f"variant {raw!r}: knockout and cutout exclude each other")
    auto = "+".join(s.replace("mono:#", "mono-") for s in steps)
    return VariantSpec(label or auto, steps)


def parse_specs(text: str | None) -> list[VariantSpec]:
    """A comma list of specs; labels must be unique."""
    specs = [parse_spec(part) for part in (text or "default").split(",") if part.strip()] or [VariantSpec("default", [])]
    labels = [s.label for s in specs]
    dupes = sorted({x for x in labels if labels.count(x) > 1})
    if dupes:
        raise VariantError(f"variant names must be unique (twice: {', '.join(dupes)})")
    return specs


def _fmt(rgb: tuple[int, int, int], alpha: float | None) -> str:
    r, g, b = rgb
    return f"#{r:02x}{g:02x}{b:02x}" if alpha is None or alpha >= 1 else f"rgba({r}, {g}, {b}, {round(alpha, 3)})"


def _algo(name: str) -> Callable[[Color], tuple[int, int, int]]:
    if name == "invert":
        return lambda c: (255 - c.r, 255 - c.g, 255 - c.b)
    if name == "mono-black":
        return lambda c: (0, 0, 0)
    if name == "mono-white":
        return lambda c: (255, 255, 255)
    if name.startswith("mono:"):
        fixed = parse_color(name[5:])
        if fixed is None:
            raise VariantError(f"unknown variant {name!r}")
        return lambda c: (fixed.r, fixed.g, fixed.b)
    if name == "grayscale":
        def gray(c: Color) -> tuple[int, int, int]:
            y = round(0.2126 * c.r + 0.7152 * c.g + 0.0722 * c.b)
            return (y, y, y)
        return gray
    raise VariantError(f"unknown variant {name!r}")


def _ensure_root_fill(text: str, colour: str) -> str:
    """Shapes without a fill render black. A variant must recolour those too, so give the root a default fill."""
    m = _ROOT.search(text)
    if not m or re.search(r"(?<![\w:-])fill\s*=", m.group(0)):
        return text
    tag = m.group(0)
    new = tag[:-2] + f' fill="{colour}"/>' if tag.endswith("/>") else tag[:-1] + f' fill="{colour}">'
    return text[:m.start()] + new + text[m.end():]


def apply_algorithmic(name: str, text: str) -> str:
    fn = _algo(name)
    out = map_colours(text, lambda _tok, c: _fmt(fn(c), c.alpha))
    return _ensure_root_fill(out, _fmt(fn(Color(0, 0, 0, None)), None))


def _own_tokens(view: dict, brands: list[str], brand: str | None) -> dict:
    """Drop other brands' primitive colour ramps (--color-petbook-…): they are noise when matching a Bild logo."""
    others = [b for b in brands if b != brand]
    if not others:
        return view
    return {n: tok for n, tok in view.items() if not any(n.startswith(f"--color-{o}-") for o in others)}


NEUTRAL_TOLERANCE = 12.0


def _is_neutral(color: Color) -> bool:
    from mysuite.color.lab import rgb_to_lab

    _, a, b = rgb_to_lab(color.r, color.g, color.b)
    return (a * a + b * b) ** 0.5 < 10.0


def choose_dark(color: Color, view: dict, tolerance: float) -> tuple[str, str, list[str]] | None:
    """The dark-surface value for a logo colour: among tokens that have a distinct dark value and whose light value is
    within `tolerance` (CIEDE2000) of the colour, take the closest group, then the dark value most of them agree on.
    Returns (dark colour, representative token, other dark values that were possible) or None."""
    from collections import Counter

    from mysuite.color.lab import delta_e_rgb

    near: list[tuple[float, str, str]] = []
    for name, token in view.items():
        if not token.has_dark:
            continue
        light, dark = token.color("light"), token.color("dark")
        if light is None or dark is None or (light.alpha is not None and light.alpha < 1) or (dark.alpha is not None and dark.alpha < 1):
            continue
        d = delta_e_rgb(tuple(color[:3]), tuple(light[:3]))
        if d <= tolerance:
            near.append((d, name, dark.hex()))
    if not near:
        return None
    best = min(d for d, _, _ in near)
    group = [(n, dk) for d, n, dk in near if d <= best + 0.5]
    votes = Counter(dk for _, dk in group)
    top = max(votes.values())
    winner = sorted((dk for dk, c in votes.items() if c == top), key=lambda h: -sum(parse_color(h)[:3]))[0]     # tie: the lighter one
    rep = sorted(n for n, dk in group if dk == winner)[0]
    others = sorted(set(dk for dk in votes if dk != winner))
    return winner, rep, others


def apply_negative(text: str, view: dict, palette_hexes: list[str], *, tolerance: float = 5.0,
                   overrides: dict[str, str] | None = None) -> tuple[str, list[str]]:
    """Token-driven on-dark variant: every logo colour becomes the dark-theme value of the design token it matches.
    `overrides` ({logo colour: dark colour}) wins; colours with no match stay as they are. Returns (svg, notes)."""
    if not any(tok.has_dark for tok in view.values()):
        raise VariantError("the tokens have no dark-theme values, so a token-driven negative can't be made - use invert or mono-white")
    mapping: dict[str, str] = {}
    notes: list[str] = []
    for hex_ in palette_hexes:
        c = parse_color(hex_)
        if c is None or (c.alpha is not None and c.alpha < 1):
            continue
        if overrides and c.hex() in overrides:
            mapping[c.hex()] = overrides[c.hex()]
            continue
        chosen = choose_dark(c, view, tolerance)
        approximate = False
        if chosen is None and _is_neutral(c):
            chosen = choose_dark(c, view, max(tolerance, NEUTRAL_TOLERANCE))      # real logos' blacks are rarely exactly the token's
            approximate = chosen is not None
        if chosen is None:
            notes.append(f"{c.hex()}: no token with a dark value within ΔE {tolerance:g}; left as it was - pick one with --negative-map")
            continue
        dark, token_name, others = chosen
        if approximate:
            notes.append(f"{c.hex()} -> {dark}: approximate, nearest neutral token {token_name} (ΔE up to {NEUTRAL_TOLERANCE:g})")
        mapping[c.hex()] = dark
        if others:
            notes.append(f"{c.hex()} -> {dark} (via {token_name}); other tokens of that colour use {', '.join(others)} - "
                         f"pick one with --negative-map \"{c.hex()}=token:NAME\"")
    out = recolor_text(text, mapping, tolerance=0.0) if mapping else text
    # shapes with no fill render black: they need a light default on a dark surface
    black = parse_color("#000000")
    if overrides and "#000000" in overrides:
        default_fill = overrides["#000000"]
    else:
        near_black = choose_dark(black, view, max(tolerance, 12.0))
        default_fill = near_black[0] if near_black else "#ffffff"
        if not near_black and _ROOT.search(text) and not re.search(r"(?<![\w:-])fill\s*=", _ROOT.search(text).group(0)):
            notes.append("shapes without a fill were made white (no token matches black)")
    return _ensure_root_fill(out, default_fill), notes
