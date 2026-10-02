from __future__ import annotations

from dataclasses import dataclass, field

from mysuite.color.parse import Color, parse_color

THEMES = ("light", "dark")


class TokenError(ValueError):
    pass


@dataclass
class Token:
    name: str                              # "--bg-color-brand-solid" (CSS) or "brand.red" (DTCG)
    brand: str | None = None               # None = global (primitives)
    values: dict[str, str] = field(default_factory=dict)   # theme -> resolved colour text ("#dd0000", "rgba(...)")
    description: str | None = None
    source: str | None = None              # file it came from

    def __post_init__(self) -> None:
        self.values = {theme: (v.lower() if v.lstrip().startswith("#") else v) for theme, v in self.values.items()}

    def color(self, theme: str = "light") -> Color | None:
        text = self.values.get(theme) or self.values.get("light")
        return parse_color(text) if text else None

    @property
    def has_dark(self) -> bool:
        light, dark = self.color("light"), self.values.get("dark")
        return bool(dark) and parse_color(dark) is not None and (light is None or parse_color(dark)[:3] != light[:3])


class TokenSet:
    """Colour tokens, keyed by (brand, name). Global tokens (brand None) are visible from every brand."""

    def __init__(self) -> None:
        self._tokens: dict[tuple[str | None, str], Token] = {}
        self.sources: list[str] = []
        self.skipped_non_colour = 0

    def add(self, token: Token) -> None:
        key = (token.brand, token.name)
        existing = self._tokens.get(key)
        if existing is None:
            self._tokens[key] = token
        else:
            existing.values.update(token.values)            # a later file overrides an earlier one, per theme
            existing.description = existing.description or token.description

    def __len__(self) -> int:
        return len(self._tokens)

    @property
    def brands(self) -> list[str]:
        return sorted({b for b, _ in self._tokens if b})

    def resolve_brand(self, brand: str | None) -> str | None:
        """The brand to use: the given one, the only one there is, or an error that lists the choices."""
        brands = self.brands
        if brand:
            if brand not in brands and any(b for b in brands):
                raise TokenError(f"unknown brand {brand!r} - this token source has: {', '.join(brands)}")
            return brand if brand in brands else None
        if len(brands) > 1:
            raise TokenError(f"this token source has several brands ({', '.join(brands)}): pick one with --brand")
        return brands[0] if brands else None

    def view(self, brand: str | None = None) -> dict[str, Token]:
        """name -> token, with the brand's own tokens overriding the global ones."""
        out = {n: t for (b, n), t in self._tokens.items() if b is None}
        if brand:
            out.update({n: t for (b, n), t in self._tokens.items() if b == brand})
        return out

    def get(self, name: str, brand: str | None = None) -> Token | None:
        view = self.view(self.resolve_brand(brand))
        return view.get(name) or view.get("--" + name.lstrip("-"))      # `bg-color-brand-solid` finds `--bg-color-brand-solid`

    def names(self, brand: str | None = None) -> list[str]:
        return sorted(self.view(self.resolve_brand(brand)))

    def colour_map(self, brand: str | None, theme: str = "light") -> dict[str, Color]:
        out: dict[str, Color] = {}
        for name, token in self.view(self.resolve_brand(brand)).items():
            c = token.color(theme)
            if c is not None:
                out[name] = c
        return out
