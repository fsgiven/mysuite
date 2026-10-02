from __future__ import annotations

from dataclasses import dataclass

from mysuite.color.lab import delta_e2000, rgb_to_lab
from mysuite.color.parse import Color
from mysuite.tokens.model import Token

EXACT = 0.5          # CIEDE2000 below this counts as "the same colour" (rounding, not a different shade)


@dataclass
class Match:
    colour: str                  # "#dd0000"
    uses: int
    token: str | None
    delta_e: float | None
    exact: bool
    alternatives: list[tuple[str, float]]


def _ranked(color: Color, view: dict[str, Token], theme: str) -> list[tuple[float, bool, str]]:
    lab = rgb_to_lab(color.r, color.g, color.b)
    out = []
    for name, token in view.items():
        c = token.color(theme)
        if c is None or c.alpha is not None and c.alpha < 1:
            continue
        d = 0.0 if c[:3] == color[:3] else delta_e2000(lab, rgb_to_lab(c.r, c.g, c.b))
        out.append((round(d, 3), not token.has_dark, name))      # ties: prefer tokens that have a dark counterpart (semantic ones)
    out.sort(key=lambda r: (r[0], r[1], len(r[2]), r[2]))
    return out


def match_palette(palette: list[tuple[str, int]], view: dict[str, Token], tolerance: float, theme: str = "light") -> list[Match]:
    from mysuite.color.parse import parse_color

    matches: list[Match] = []
    for hex_, uses in palette:
        color = parse_color(hex_)
        if color is None:
            continue
        ranked = _ranked(color, view, theme)
        best = ranked[0] if ranked and ranked[0][0] <= tolerance else None
        matches.append(Match(
            colour=hex_, uses=uses, token=best[2] if best else None, delta_e=best[0] if best else None,
            exact=bool(best and best[0] <= EXACT),
            alternatives=[(n, d) for d, _, n in ranked[:3] if not best or n != best[2]],
        ))
    return matches
