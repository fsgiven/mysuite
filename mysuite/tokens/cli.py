from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.markup import escape
from rich.table import Table

from mysuite.color.svg import palette
from mysuite.convert._parsing import RASTER_FORMATS, detect_source_format
from mysuite.tokens.match import match_palette
from mysuite.tokens.model import TokenError
from mysuite.tokens.sources import load_tokens
from mysuite.utils import jsonout
from mysuite.utils.console import console, log_error, log_skip

app = typer.Typer(help="Design tokens: list them, look one up, check a logo's colours against them.", no_args_is_help=True)

SOURCE = typer.Argument(..., help="Token source: a .css/.json file or folder, git+https://host/org/repo@REF#path=dir, or figma:FILEKEY.")
BRAND = typer.Option(None, "--brand", help="Which brand (when the source has several, e.g. an Axis folder).")
THEME = typer.Option("light", "--theme", help="light or dark.")
REFRESH = typer.Option(False, "--tokens-refresh", help="Fetch a git source again instead of using the cached copy.")


def _load(source: str, brand: Optional[str], refresh: bool):
    try:
        loaded = load_tokens(source, refresh=refresh)
        resolved_brand = loaded.tokens.resolve_brand(brand)
    except TokenError as exc:
        log_error(escape(str(exc)))
        raise typer.Exit(1) from exc
    jsonout.set_extra(source=loaded.description, commit=loaded.commit, brand=resolved_brand, brands=loaded.tokens.brands,
                      token_count=len(loaded.tokens), non_colour_skipped=loaded.tokens.skipped_non_colour)
    return loaded, resolved_brand


@app.command("list", help="List the colour tokens of a source.")
@jsonout.with_json("tokens-list")
def list_tokens(
    source: str = SOURCE, brand: Optional[str] = BRAND,
    filter_: Optional[str] = typer.Option(None, "--filter", "-f", help="Only names containing this text."),
    only_dark: bool = typer.Option(False, "--has-dark", help="Only tokens that have a different dark-theme value."),
    refresh: bool = REFRESH,
) -> None:
    loaded, b = _load(source, brand, refresh)
    view = loaded.tokens.view(b)
    table = Table(title=f"{len(view)} colour tokens" + (f" - {b}" if b else ""))
    for col in ("token", "light", "dark"):
        table.add_column(col)
    shown = 0
    for name in sorted(view):
        t = view[name]
        if filter_ and filter_.lower() not in name.lower():
            continue
        if only_dark and not t.has_dark:
            continue
        shown += 1
        jsonout.add_item(token=name, light=t.values.get("light"), dark=t.values.get("dark"), has_dark=t.has_dark, brand=t.brand, status="ok")
        table.add_row(escape(name), t.values.get("light", ""), t.values.get("dark", ""))
    console.print(table)
    if len(loaded.tokens.brands) > 1 and not brand:
        pass
    if loaded.tokens.brands:
        console.print(f"[dim]brands: {', '.join(loaded.tokens.brands)}[/dim]")


@app.command("show", help="One token: its values per theme and where it came from.")
@jsonout.with_json("tokens-show")
def show(source: str = SOURCE, name: str = typer.Argument(..., help="Token name, e.g. --bg-color-brand-solid or brand.red."),
         brand: Optional[str] = BRAND, refresh: bool = REFRESH) -> None:
    loaded, b = _load(source, brand, refresh)
    token = loaded.tokens.get(name, b)
    if token is None:
        near = [n for n in loaded.tokens.names(b) if name.lstrip("-").lower() in n.lower()][:8]
        log_error(f"no token named {escape(name)}" + (f" - did you mean: {escape(', '.join(near))}" if near else ""))
        raise typer.Exit(1)
    jsonout.add_item(token=token.name, brand=token.brand, values=token.values, description=token.description, source=token.source, status="ok")
    for theme, value in token.values.items():
        console.print(f"{escape(token.name)}  [bold]{theme}[/bold]  {escape(value)}")


@app.command("check", help="Check a logo's colours against the tokens: which are on-palette, which are off. Exit 1 with --strict if any is off.")
@jsonout.with_json("tokens-check")
def check(
    logo: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True, help="SVG (or raster image) to check."),
    tokens: str = typer.Option(..., "--tokens", help="Token source (file, folder, git+… or figma:KEY)."),
    brand: Optional[str] = BRAND, theme: str = THEME,
    tolerance: float = typer.Option(2.0, "--tolerance", min=0.0, help="CIEDE2000 distance that still counts as the token (0 = exact)."),
    strict: bool = typer.Option(False, "--strict", help="Exit with code 1 if any colour is not a token colour."),
    refresh: bool = REFRESH,
) -> None:
    from mysuite import sandbox

    sandbox.check_read(logo.resolve())
    loaded, b = _load(tokens, brand, refresh)
    from mysuite.color.variants import _own_tokens

    kind = detect_source_format(logo)
    if kind == "svg":
        pal = palette(logo.read_text(encoding="utf-8", errors="replace"))
    elif kind in RASTER_FORMATS:
        from PIL import Image

        from mysuite.inspect.inspect import _palette_of

        with Image.open(logo) as im:
            im.seek(0)
            pal = [(c["hex"], round(c["share"] * 100)) for c in _palette_of(im, 8)]
    else:
        log_error("check needs an SVG or a raster image")
        raise typer.Exit(1)
    view = _own_tokens(loaded.tokens.view(b), loaded.tokens.brands, b)       # other brands' colour ramps are not candidates
    matches = match_palette([(h[:7], n) for h, n in pal], view, tolerance, theme)
    table = Table(title=f"{logo.name} against {len(view)} tokens ({theme})")
    for col in ("colour", "uses", "token", "ΔE"):
        table.add_column(col)
    off = 0
    for m in matches:
        if m.token is None:
            off += 1
        jsonout.add_item(colour=m.colour, uses=m.uses, token=m.token, delta_e=m.delta_e, exact=m.exact,
                         alternatives=[{"token": n, "delta_e": d} for n, d in m.alternatives], status="on-palette" if m.token else "off-palette")
        table.add_row(m.colour, str(m.uses), escape(m.token) if m.token else "[#F87171]off palette[/]",
                      "" if m.delta_e is None else f"{m.delta_e:g}" + ("" if m.exact else " (near)"))
    console.print(table)
    jsonout.set_extra(on_palette=len(matches) - off, off_palette=off, tolerance=tolerance, theme=theme)
    for m in matches:
        if m.token and not m.exact:
            jsonout.add_warning(f"{m.colour} is only close to {m.token} (ΔE {m.delta_e})")
    if off:
        log_skip(f"{off} colour(s) are not in the token set")
        if strict:
            raise typer.Exit(1)
