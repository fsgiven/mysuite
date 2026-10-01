from __future__ import annotations

import re
import tempfile
from pathlib import Path

_HEX_RE = re.compile(r"^#?[0-9a-fA-F]{3}$|^#?[0-9a-fA-F]{4}$|^#?[0-9a-fA-F]{6}$|^#?[0-9a-fA-F]{8}$")

# The 147 standard CSS3/SVG named colors. Deliberately excludes "transparent"
# and "currentColor" — those have special inheritance/alpha semantics, not a
# single fixed color, so swapping them out as literal text would be wrong.
_SVG_NAMED_COLORS = frozenset({
    "aliceblue", "antiquewhite", "aqua", "aquamarine", "azure", "beige", "bisque",
    "black", "blanchedalmond", "blue", "blueviolet", "brown", "burlywood",
    "cadetblue", "chartreuse", "chocolate", "coral", "cornflowerblue", "cornsilk",
    "crimson", "cyan", "darkblue", "darkcyan", "darkgoldenrod", "darkgray",
    "darkgreen", "darkgrey", "darkkhaki", "darkmagenta", "darkolivegreen",
    "darkorange", "darkorchid", "darkred", "darksalmon", "darkseagreen",
    "darkslateblue", "darkslategray", "darkslategrey", "darkturquoise",
    "darkviolet", "deeppink", "deepskyblue", "dimgray", "dimgrey", "dodgerblue",
    "firebrick", "floralwhite", "forestgreen", "fuchsia", "gainsboro",
    "ghostwhite", "gold", "goldenrod", "gray", "grey", "green", "greenyellow",
    "honeydew", "hotpink", "indianred", "indigo", "ivory", "khaki", "lavender",
    "lavenderblush", "lawngreen", "lemonchiffon", "lightblue", "lightcoral",
    "lightcyan", "lightgoldenrodyellow", "lightgray", "lightgreen", "lightgrey",
    "lightpink", "lightsalmon", "lightseagreen", "lightskyblue", "lightslategray",
    "lightslategrey", "lightsteelblue", "lightyellow", "lime", "limegreen",
    "linen", "magenta", "maroon", "mediumaquamarine", "mediumblue",
    "mediumorchid", "mediumpurple", "mediumseagreen", "mediumslateblue",
    "mediumspringgreen", "mediumturquoise", "mediumvioletred", "midnightblue",
    "mintcream", "mistyrose", "moccasin", "navajowhite", "navy", "oldlace",
    "olive", "olivedrab", "orange", "orangered", "orchid", "palegoldenrod",
    "palegreen", "paleturquoise", "palevioletred", "papayawhip", "peachpuff",
    "peru", "pink", "plum", "powderblue", "purple", "rebeccapurple", "red",
    "rosybrown", "royalblue", "saddlebrown", "salmon", "sandybrown", "seagreen",
    "seashell", "sienna", "silver", "skyblue", "slateblue", "slategray",
    "slategrey", "snow", "springgreen", "steelblue", "tan", "teal", "thistle",
    "tomato", "turquoise", "violet", "wheat", "white", "whitesmoke", "yellow",
    "yellowgreen",
})


class InvalidRecolorError(ValueError):
    pass


def _normalize_color(value: str) -> str:
    """Normalizes a FROM/TO color spec — either a hex color (returned as
    "#hex") or a recognized CSS/SVG named color (returned lowercased, with no
    "#" — this absence of "#" is what apply_recolor() uses downstream to pick
    the matching strategy)."""
    stripped = value.strip()
    if _HEX_RE.match(stripped):
        return stripped if stripped.startswith("#") else f"#{stripped}"
    lowered = stripped.lower()
    if lowered in _SVG_NAMED_COLORS:
        return lowered
    raise InvalidRecolorError(
        f"invalid color {value!r} — expected a hex color like #fff, #ffffff, #ffffffaa, "
        f"or a named CSS color like white, black, red"
    )


def parse_recolor_pairs(pairs: list[str]) -> dict[str, str]:
    """Parses ["#000000=#ffffff", "f00=0f0", "white=black"] into a
    {from_color: to_color} map — hex sides normalized to a leading "#", named
    colors normalized to lowercase with no "#". Raises InvalidRecolorError on
    bad syntax or an unrecognized color."""
    mapping: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise InvalidRecolorError(
                f"invalid --recolor {pair!r} — expected FROM=TO, e.g. #000000=#ffffff or white=black"
            )
        from_raw, to_raw = pair.split("=", 1)
        mapping[_normalize_color(from_raw)] = _normalize_color(to_raw)
    return mapping


_FIELD_SPLIT_RE = re.compile(r"[,\s]+")


def parse_recolor_field(value: str) -> dict[str, str]:
    """Parses a single free-text field containing one or more FROM=TO pairs,
    separated by commas and/or whitespace in any mix — "#fff=#000 #f00=#0f0",
    "#fff=#000,#f00=#0f0", and "white=black, red=blue" all work the same way.
    Used by the CLI's repeatable --recolor flag (each occurrence may itself
    contain multiple pairs) via parse_recolor_list()."""
    stripped = value.strip()
    if not stripped:
        return {}
    pairs = [p for p in _FIELD_SPLIT_RE.split(stripped) if p]
    return parse_recolor_pairs(pairs)


def parse_recolor_list(items: list[str]) -> dict[str, str]:
    """Parses a list of --recolor occurrences, where each item may itself
    contain one or more comma/whitespace-separated FROM=TO pairs — so both
    `--recolor A --recolor B` and `--recolor "A B"` work the same way."""
    mapping: dict[str, str] = {}
    for item in items:
        mapping.update(parse_recolor_field(item))
    return mapping


def _hex_pattern_for(hex_color: str) -> re.Pattern[str]:
    # Match the exact hex token as a whole color literal — not as a substring of
    # a longer hex run — so recoloring #fff never touches #ffffff or #fffabc.
    # Deliberately a blind whole-document match, not scoped to any particular
    # XML context: a "#RRGGBB"-shaped string has essentially zero collision
    # risk of appearing except as an actual color value.
    escaped = re.escape(hex_color.lstrip("#"))
    return re.compile(rf"#{escaped}(?![0-9a-fA-F])", re.IGNORECASE)


def _named_color_attr_pattern_for(name: str) -> re.Pattern[str]:
    # fill="white" / stroke='white' / stop-color="white". The (?<![\w-])
    # lookbehind stops this from also matching inside e.g. a hypothetical
    # data-fill="white" attribute.
    escaped = re.escape(name)
    return re.compile(
        rf'(?<![\w-])(fill|stroke|stop-color)(\s*=\s*)(["\']){escaped}(["\'])',
        re.IGNORECASE,
    )


def _named_color_style_pattern_for(name: str) -> re.Pattern[str]:
    # style="...fill:white;..." / style="...stroke: white ..."
    escaped = re.escape(name)
    return re.compile(
        rf'(?<![\w-])(fill|stroke|stop-color)(\s*:\s*){escaped}(?=\s*[;"\'])',
        re.IGNORECASE,
    )


def _apply_named_color(text: str, name: str, replacement: str) -> str:
    attr_pattern = _named_color_attr_pattern_for(name)
    text = attr_pattern.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{replacement}{m.group(4)}", text
    )
    style_pattern = _named_color_style_pattern_for(name)
    text = style_pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}{replacement}", text)
    return text


def apply_recolor(input_svg: Path, recolor_map: dict[str, str]) -> tuple[Path, bool]:
    """Applies FROM->TO color substitutions to an SVG's raw text. Returns
    (path, is_temp): the original path unchanged if recolor_map is empty,
    otherwise a new temporary file the caller is responsible for deleting
    after use.

    Two matching strategies, chosen per FROM key by whether it starts with
    "#": a hex FROM does a blind whole-document match of the literal #hex
    token (fill="#xxx", style="fill:#xxx", <style> CSS blocks — anywhere it
    appears). A named-color FROM (e.g. "white") is matched only when it's the
    value of a fill=/stroke=/stop-color= attribute or an inline
    style="fill:...;" declaration — never a blind whole-document match, since
    plain English color words collide with unrelated content (ids, class
    names, <title> text) far more than a "#RRGGBB"-shaped token ever would.
    Note this named-color scope does NOT reach a bare <style>.cls{fill:white}</style>
    CSS block with no style= wrapper — out of scope for now."""
    if not recolor_map:
        return input_svg, False

    text = input_svg.read_text(encoding="utf-8")
    for from_color, to_color in recolor_map.items():
        if from_color.startswith("#"):
            text = _hex_pattern_for(from_color).sub(to_color, text)
        else:
            text = _apply_named_color(text, from_color, to_color)

    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".svg", prefix=f"mysuite-recolor-{input_svg.stem}-", delete=False
    )
    try:
        tmp.write(text)
    finally:
        tmp.close()
    return Path(tmp.name), True
