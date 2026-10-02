from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

import typer
from rich.markup import escape

from mysuite.color import variants as variant_mod
from mysuite.color.svg import palette
from mysuite.tokens.context import build_context
from mysuite.tokens.model import TokenError
from mysuite.utils import jsonout
from mysuite.utils.console import console, log_error, log_skip, log_step
from mysuite.utils.subprocess_utils import atomic_write_via

app = typer.Typer(help="Colour variants of an SVG logo: negative / on-dark (from design tokens), invert, mono black/white, grayscale.", no_args_is_help=True)


@app.command("list", help="The available variants and what each does.")
@jsonout.with_json("variants-list")
def list_variants() -> None:
    about = {
        "default": "the logo unchanged",
        "negative": "token-driven on-dark version: every colour becomes the dark-theme value of the design token it matches (needs --tokens)",
        "on-dark": "same as negative",
        "invert": "every colour inverted (white <-> black, red -> cyan)",
        "mono-black": "everything black, transparency kept",
        "mono-white": "everything white, transparency kept",
        "grayscale": "luminance-only version",
    }
    for name in variant_mod.ALL:
        jsonout.add_item(variant=name, description=about[name], needs_tokens=name in variant_mod.TOKEN_DRIVEN, status="ok")
        console.print(f"[bold]{name}[/bold]  [dim]{about[name]}[/dim]")


@app.command("make", help="Write SVG variants of a logo: `mysuite variants make logo.svg --variants negative,mono-white --tokens tokens/ --brand acme`.")
@jsonout.with_json("variants")
def make(
    inputs: List[Path] = typer.Argument(..., exists=True, dir_okay=False, readable=True, help="SVG logo(s)."),
    variants: str = typer.Option("negative,mono-white,mono-black", "--variants", help=f"Comma-separated: {', '.join(variant_mod.ALL[1:])}."),
    tokens: Optional[str] = typer.Option(None, "--tokens", help="Design tokens: file/folder (.css/.json), git+https://…@REF#path=dir, or figma:KEY. Needed for negative."),
    brand: Optional[str] = typer.Option(None, "--brand", help="Which brand of the token source."),
    negative_map: Optional[List[str]] = typer.Option(None, "--negative-map", help="LOGOCOLOUR=token:NAME: use that token's DARK value for that logo colour (repeatable)."),
    negative_tolerance: float = typer.Option(5.0, "--negative-tolerance", min=0.0, help="How close (CIEDE2000) a logo colour must be to a token to use it."),
    out: Optional[Path] = typer.Option(None, "--out", "-o", help="Output folder (default: beside the logo)."),
    tokens_refresh: bool = typer.Option(False, "--tokens-refresh", help="Fetch a git token source again."),
    overwrite: bool = typer.Option(False, "--overwrite/--no-overwrite"),
    dry_run: bool = typer.Option(False, "--dry-run", help="List the files that would be written."),
) -> None:
    from mysuite.export.recolor import make_variant_svg

    names = [v.strip().lower() for v in variants.split(",") if v.strip()]
    bad = [v for v in names if v not in variant_mod.ALL or v == "default"]
    if not names or bad or len(set(names)) != len(names):
        log_error("variants must be unique and from: " + ", ".join(variant_mod.ALL[1:]) + (f" (got {', '.join(bad)})" if bad else ""))
        raise typer.Exit(1)
    ctx = None
    if tokens:
        try:
            ctx = build_context(tokens, brand, negative_map, tokens_refresh)
        except TokenError as exc:
            log_error(escape(str(exc)))
            raise typer.Exit(1) from exc
        jsonout.set_extra(tokens={"source": ctx.loaded.description, "commit": ctx.loaded.commit, "brand": ctx.brand})
    elif any(v in variant_mod.TOKEN_DRIVEN for v in names):
        log_error("the negative variant is driven by design tokens: pass --tokens SOURCE (or use invert / mono-white)")
        raise typer.Exit(1)

    failed = 0
    for logo in inputs:
        if logo.suffix.lower() != ".svg":
            failed += 1
            jsonout.add_item(input=logo, status="failed", error="variants work on SVG logos")
            log_error(f"{escape(str(logo))}: variants work on SVG logos")
            continue
        folder = Path(os.path.abspath(out)) if out else Path(os.path.abspath(logo)).parent
        for v in names:
            target = folder / f"{logo.stem}_{v}.svg"
            if dry_run:
                jsonout.add_item(input=logo, output=target, variant=v, status="planned")
                console.print(f"[dim]{escape(str(target))}[/dim]")
                continue
            if target.exists() and not overwrite:
                jsonout.add_item(input=logo, output=target, variant=v, status="skipped_existing")
                console.print(f"[dim]— exists, skipped: {escape(str(target))}[/dim]")
                continue
            try:
                tmp_svg, _, notes = make_variant_svg(Path(os.path.abspath(logo)), False, v, ctx.view if ctx else None,
                                                     overrides=ctx.overrides if ctx else None, tolerance=negative_tolerance)
            except (variant_mod.VariantError, TokenError, UnicodeDecodeError) as exc:
                failed += 1
                jsonout.add_item(input=logo, variant=v, status="failed", error=str(exc))
                log_error(f"{escape(str(logo))} ({v}): {escape(str(exc))}")
                continue
            try:
                text = tmp_svg.read_text(encoding="utf-8")
            finally:
                tmp_svg.unlink(missing_ok=True)
            atomic_write_via(target, lambda tmp, text=text: tmp.write_text(text, encoding="utf-8"), preserve_extension=True)
            jsonout.add_item(input=logo, output=target, variant=v, status="written", colours=[h for h, _ in palette(text)], notes=notes)
            log_step(escape(str(target)))
            for n in notes:
                jsonout.add_warning(f"{logo.name} {v}: {n}")
                log_skip(escape(f"{v}: {n}"))
    if failed:
        raise typer.Exit(1)
