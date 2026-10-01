from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import typer
from rich.markup import escape
from rich.table import Table

from mysuite.config import BUILT_IN_PRESETS, MysuiteConfigError, load_config
from mysuite.config_write import save_preset
from mysuite.export._parsing import parse_csv
from mysuite.export.recolor import InvalidRecolorError, parse_recolor_list
from mysuite.utils.console import console, log_error

app = typer.Typer(help="Manage export presets — built-in ready-made ones and your own saved bundles.")


@app.command("list", help="List available presets (built-in and from mysuite.toml).")
def list_presets(
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Explicit path to mysuite.toml."),
) -> None:
    try:
        config = load_config(config_path)
    except MysuiteConfigError as exc:
        log_error(str(exc))
        raise typer.Exit(1) from exc

    table = Table(title="mysuite presets")
    table.add_column("name")
    table.add_column("source")
    table.add_column("settings")

    for name in sorted(config.presets):
        is_builtin = name in BUILT_IN_PRESETS and config.presets[name] == BUILT_IN_PRESETS[name]
        source = "built-in" if is_builtin else "custom"
        settings_str = ", ".join(f"{k}={v}" for k, v in config.presets[name].items())
        table.add_row(name, source, settings_str)

    console.print(table)


@app.command("save", help="Save export settings as a new (or replacement) preset in mysuite.toml.")
def save(
    name: str = typer.Argument(..., help="Preset name."),
    sizes: Optional[str] = typer.Option(
        None, "--sizes", "-s", help="Comma-separated sizes, e.g. 16,32,5cm,2in."
    ),
    formats: Optional[str] = typer.Option(None, "--formats", "-f", help="Comma-separated formats."),
    profiles: Optional[str] = typer.Option(None, "--profiles", "-p", help="Comma-separated: rgb,cmyk."),
    dpi: Optional[float] = typer.Option(None, "--dpi", "--ppi", help="Pixels/dots per inch."),
    unit: Optional[str] = typer.Option(None, "--unit", help="Default unit for bare sizes: px, mm, cm, in."),
    background: Optional[str] = typer.Option(None, "--background", help="Background color."),
    quality: Optional[int] = typer.Option(None, "--quality", min=0, max=100, help="JPEG/WebP quality."),
    padding: Optional[str] = typer.Option(
        None, "--padding", "--margin", help="Uniform margin baseline, e.g. 20px or 10%%."
    ),
    margin_top: Optional[str] = typer.Option(None, "--margin-top", help="Top margin override."),
    margin_right: Optional[str] = typer.Option(None, "--margin-right", help="Right margin override."),
    margin_bottom: Optional[str] = typer.Option(None, "--margin-bottom", help="Bottom margin override."),
    margin_left: Optional[str] = typer.Option(None, "--margin-left", help="Left margin override."),
    png_compression: Optional[int] = typer.Option(
        None, "--png-compression", min=0, max=9, help="PNG compression level."
    ),
    out: Optional[str] = typer.Option(None, "--out", "-o", help="Base output directory."),
    date_stamp: Optional[bool] = typer.Option(None, "--date-stamp/--no-date-stamp", help="Insert today's date into filenames."),
    variant: Optional[str] = typer.Option(None, "--variant", help="Variant label, e.g. negative, mono."),
    recolor: Optional[List[str]] = typer.Option(
        None, "--recolor", help="Exact hex color substitution FROM=TO. Repeatable."
    ),
    strict: Optional[bool] = typer.Option(None, "--strict/--no-strict"),
    overwrite: Optional[bool] = typer.Option(None, "--overwrite/--no-overwrite"),
    normalize_png: Optional[bool] = typer.Option(None, "--normalize-png/--no-normalize-png"),
    config_path: Optional[Path] = typer.Option(
        None, "--config", "-c", help="TOML file to save into (default: ./mysuite.toml)."
    ),
) -> None:
    settings: dict = {}
    if sizes is not None:
        settings["sizes"] = parse_csv(sizes)
    if formats is not None:
        settings["formats"] = parse_csv(formats)
    if profiles is not None:
        settings["profiles"] = parse_csv(profiles)
    if dpi is not None:
        settings["dpi"] = dpi
    if unit is not None:
        settings["default_unit"] = unit
    if background is not None:
        settings["background"] = background
    if quality is not None:
        settings["quality"] = quality
    if padding is not None:
        settings["padding"] = padding
    if margin_top is not None:
        settings["margin_top"] = margin_top
    if margin_right is not None:
        settings["margin_right"] = margin_right
    if margin_bottom is not None:
        settings["margin_bottom"] = margin_bottom
    if margin_left is not None:
        settings["margin_left"] = margin_left
    if png_compression is not None:
        settings["png_compression"] = png_compression
    if out is not None:
        settings["out_dir"] = out
    if date_stamp is not None:
        settings["date_stamp"] = date_stamp
    if variant is not None:
        settings["variant"] = variant
    if recolor:
        try:
            settings["recolor"] = parse_recolor_list(recolor)
        except InvalidRecolorError as exc:
            log_error(str(exc))
            raise typer.Exit(1) from exc
    if strict is not None:
        settings["strict"] = strict
    if overwrite is not None:
        settings["overwrite"] = overwrite
    if normalize_png is not None:
        settings["normalize_png"] = normalize_png

    if not settings:
        log_error("nothing to save — pass at least one setting flag (e.g. --sizes, --formats)")
        raise typer.Exit(1)

    target = config_path or (Path.cwd() / "mysuite.toml")
    save_preset(target, name, settings)
    console.print(f"[bold green]saved[/bold green] preset {name!r} to {escape(str(target))}")
