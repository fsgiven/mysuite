"""Colour tokens from CSS custom-property files (the format design systems like Axis ship).

Handles: `--name: value;` declarations inside any rule, `var(--other, fallback)` aliases (resolved against the
same brand/theme, then the brand's default theme, then global primitives, then the written fallback), per-brand
scopes via `[data-color-brand="x"]`, light/dark themes via `[data-theme="dark"]` or an
`@media (prefers-color-scheme: dark)` block. Non-colour custom properties (sizes, fonts…) are counted, not kept.
"""
from __future__ import annotations

import re
from pathlib import Path

from mysuite.color.parse import parse_color
from mysuite.tokens.model import Token, TokenSet

_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_DECL = re.compile(r"(--[\w-]+)\s*:\s*([^;{}]+?)\s*(?:;|$)")
_VAR = re.compile(r"var\(\s*(--[\w-]+)\s*(?:,\s*([^()]*(?:\([^()]*\)[^()]*)*))?\)")
_BRAND = re.compile(r"""data-color-brand\s*=\s*["']([^"']+)["']""")
_THEME = re.compile(r"""data-theme\s*=\s*["'](light|dark)["']""")
MAX_DEPTH = 12


def _blocks(text: str) -> list[tuple[str, str, bool]]:
    """(selector, body, in_dark_media) for every rule, flattening @media nesting."""
    out: list[tuple[str, str, bool]] = []

    def walk(chunk: str, dark: bool) -> None:
        i, n = 0, len(chunk)
        while i < n:
            brace = chunk.find("{", i)
            if brace < 0:
                break
            head = chunk[i:brace].strip()
            depth, j = 1, brace + 1
            while j < n and depth:
                depth += (chunk[j] == "{") - (chunk[j] == "}")
                j += 1
            body = chunk[brace + 1:j - 1]
            if head.startswith("@"):
                walk(body, dark or ("prefers-color-scheme" in head and "dark" in head))
            else:
                out.append((head, body, dark))
            i = j

    walk(text, False)
    return out


def parse_css(text: str, source: str | None = None, into: TokenSet | None = None) -> TokenSet:
    return parse_css_files([(text, source)], into)


def parse_css_files(files: list[tuple[str, str | None]], into: TokenSet | None = None) -> TokenSet:
    """Several CSS files at once, so an alias in one file can point at a primitive defined in another."""
    tokens = into if into is not None else TokenSet()
    raw: dict[tuple[str | None, str, str], str] = {}        # (brand, theme, name) -> raw value
    origin: dict[tuple[str | None, str, str], str | None] = {}
    for text, source in files:
        for selector, body, dark_media in _blocks(_COMMENT.sub("", text)):
            brand_m = _BRAND.search(selector)
            theme_m = _THEME.search(selector)
            theme = theme_m.group(1) if theme_m else ("dark" if dark_media else "light")
            brand = brand_m.group(1) if brand_m else None
            for name, value in _DECL.findall(body):
                raw[(brand, theme, name)] = value.strip()
                origin[(brand, theme, name)] = source

    def lookup(brand: str | None, theme: str, name: str) -> str | None:
        for b, t, n in ((brand, theme, name), (brand, "light", name), (None, theme, name), (None, "light", name)):
            if (b, t, n) in raw:
                return raw[(b, t, n)]
        return None

    def resolve(value: str, brand: str | None, theme: str, depth: int = 0) -> str | None:
        if depth > MAX_DEPTH:
            return None
        m = _VAR.fullmatch(value.strip())
        if not m:
            return value.strip()
        target, fallback = m.group(1), m.group(2)
        found = lookup(brand, theme, target)
        if found is not None:
            r = resolve(found, brand, theme, depth + 1)
            if r is not None:
                return r
        return resolve(fallback, brand, theme, depth + 1) if fallback else None

    seen: set[tuple[str | None, str]] = set()
    for (brand, theme, name), value in raw.items():
        resolved = resolve(value, brand, theme)
        color = parse_color(resolved) if resolved else None
        if color is None:
            if (brand, name) not in seen:
                tokens.skipped_non_colour += 1
                seen.add((brand, name))
            continue
        seen.add((brand, name))
        tokens.add(Token(name=name, brand=brand, values={theme: resolved}, source=origin[(brand, theme, name)]))
    for _, source in files:
        if source:
            tokens.sources.append(source)
    return tokens


def load_css_file(path: Path, into: TokenSet | None = None) -> TokenSet:
    return parse_css(path.read_text(encoding="utf-8", errors="replace"), str(path), into)
