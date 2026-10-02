"""Colour tokens from W3C Design Tokens (DTCG) JSON, also the older Style Dictionary `value`/`type` spelling.

    {"brand": {"red": {"$type": "color", "$value": "#dd0000",
                       "$extensions": {"mysuite": {"dark": "#ff6b6b", "cmyk": [0, 100, 100, 0]}}}},
     "alias": {"$type": "color", "$value": "{brand.red}"}}

Groups may set `$type` for everything below them; aliases `{a.b}` are resolved. Themes: `$extensions.mysuite.dark`
(or `onDark`) holds the dark-surface value. Modes written as `$extensions.modes.{light,dark}` (Tokens Studio) work too.
"""
from __future__ import annotations

import json
import re
from typing import Any

from mysuite.color.parse import parse_color
from mysuite.tokens.model import Token, TokenError, TokenSet

_ALIAS = re.compile(r"^\{([^{}]+)\}$")
MAX_DEPTH = 12


def parse_dtcg(data: Any, source: str | None = None, into: TokenSet | None = None, brand: str | None = None) -> TokenSet:
    tokens = into if into is not None else TokenSet()
    if not isinstance(data, dict):
        raise TokenError("a design token file must be a JSON object")
    flat: dict[str, tuple[Any, dict[str, Any], str | None]] = {}   # path -> ($value, extensions, description)

    def walk(node: dict[str, Any], path: list[str], inherited: str | None) -> None:
        node_type = node.get("$type", node.get("type", inherited))
        if ("$value" in node) or ("value" in node and not isinstance(node.get("value"), dict)):
            value = node.get("$value", node.get("value"))
            if node_type in (None, "color"):
                flat[".".join(path)] = (value, node.get("$extensions") or {}, node.get("$description") or node.get("description"))
            else:
                tokens.skipped_non_colour += 1
            return
        for key, child in node.items():
            if key.startswith("$") or not isinstance(child, dict):
                continue
            walk(child, [*path, key], node_type)

    walk(data, [], None)

    def resolve(value: Any, depth: int = 0) -> str | None:
        if depth > MAX_DEPTH or not isinstance(value, str):
            return None
        m = _ALIAS.match(value.strip())
        if m:
            ref = flat.get(m.group(1))
            return resolve(ref[0], depth + 1) if ref else None
        return value.strip()

    for name, (value, ext, description) in flat.items():
        resolved = resolve(value)
        if resolved is None or parse_color(resolved) is None:
            tokens.skipped_non_colour += 1
            continue
        values = {"light": resolved}
        mine = ext.get("mysuite", {}) if isinstance(ext, dict) else {}
        modes = ext.get("modes", {}) if isinstance(ext, dict) else {}
        dark = mine.get("dark") or mine.get("onDark") or (modes.get("dark") if isinstance(modes, dict) else None)
        dark = resolve(dark) if dark else None
        if dark and parse_color(dark) is not None:
            values["dark"] = dark
        if isinstance(modes, dict) and modes.get("light"):
            light = resolve(modes["light"])
            if light and parse_color(light) is not None:
                values["light"] = light
        tokens.add(Token(name=name, brand=brand, values=values, description=description, source=source))
    if source:
        tokens.sources.append(source)
    return tokens


def load_json_text(text: str, source: str | None = None, into: TokenSet | None = None, brand: str | None = None) -> TokenSet:
    try:
        return parse_dtcg(json.loads(text), source, into, brand)
    except ValueError as exc:
        if isinstance(exc, TokenError):
            raise
        raise TokenError(f"{source or 'token file'}: not valid JSON ({exc})") from exc
