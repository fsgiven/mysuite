from __future__ import annotations

from dataclasses import dataclass, field

from mysuite.color import variants as variant_mod
from mysuite.color.parse import parse_color
from mysuite.tokens.model import TokenError, TokenSet
from mysuite.tokens.sources import Loaded, load_tokens


@dataclass
class TokenContext:
    loaded: Loaded
    brand: str | None
    view: dict                                   # tokens visible to this brand (other brands' primitives removed)
    overrides: dict[str, str] = field(default_factory=dict)   # logo colour -> dark colour, from --negative-map

    @property
    def tokens(self) -> TokenSet:
        return self.loaded.tokens


def build_context(spec: str, brand: str | None = None, negative_map: list[str] | None = None, refresh: bool = False) -> TokenContext:
    from mysuite.export.recolor import InvalidRecolorError, parse_recolor_list, resolve_token_refs

    loaded = load_tokens(spec, refresh=refresh)
    resolved = loaded.tokens.resolve_brand(brand)
    ctx = TokenContext(loaded, resolved, variant_mod._own_tokens(loaded.tokens.view(resolved), loaded.tokens.brands, resolved))
    for pair in negative_map or []:
        key, sep, value = pair.partition("=")
        if not sep:
            raise TokenError(f"--negative-map {pair!r} must look like '#1d1d1b=token:--headline-text-color'")
        try:
            (k, v), = parse_recolor_list([f"{key}={value}"]).items()
        except InvalidRecolorError as exc:
            raise TokenError(str(exc)) from exc
        (fk, fv), = resolve_token_refs({k: v}, loaded.tokens, brand, "dark").items()
        color = parse_color(fk)
        if color is None:
            raise TokenError(f"--negative-map: not a colour: {key!r}")
        ctx.overrides[color.hex()] = fv
    return ctx
