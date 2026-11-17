from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import typer
from rich.markup import escape
from rich.table import Table

from mysuite.config import MysuiteConfigError, load_config
from mysuite.convert._parsing import InvalidInputError, resolve_input_files
from mysuite.inspect.inspect import InspectError, inspect_file, write_contact_sheet, write_thumbnail
from mysuite.utils import jsonout
from mysuite.utils.console import console, log_error
from mysuite.utils.subprocess_utils import MysuiteToolError


@jsonout.with_json("inspect")
def inspect_command(
    inputs: List[Path] = typer.Argument(..., exists=True, readable=True, help="Files and/or folders to examine."),
    recursive: bool = typer.Option(False, "--recursive", "-r", help="Recurse into subdirectories."),
    thumb: Optional[Path] = typer.Option(
        None, "--thumb", help="Write a <=512 px PNG preview of the (single) input, for clients that can look at images."
    ),
    sheet: Optional[Path] = typer.Option(
        None, "--sheet", help="Write a labelled contact sheet PNG of all inputs."
    ),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Explicit path to mysuite.toml."),
) -> None:
    try:
        files = resolve_input_files(inputs, recursive=recursive)
        config = load_config(config_path)
    except (InvalidInputError, MysuiteConfigError) as exc:
        log_error(escape(str(exc)))
        raise typer.Exit(1) from exc

    failed = 0
    for f in files:
        try:
            info = inspect_file(f, config.tools)
        except (InspectError, MysuiteToolError) as exc:
            failed += 1
            jsonout.add_item(path=f, status="failed", error=str(exc))
            log_error(f"{escape(str(f))}: {escape(str(exc))}")
            continue
        problems = info.get("warnings", [])
        for w in problems:
            jsonout.add_warning(f"{f.name}: {w}")
        jsonout.add_item(status="warning" if problems else "ok", **info)
        _print(info)

    if thumb:
        if len(files) != 1:
            log_error("--thumb needs exactly one input (use --sheet for several)")
            raise typer.Exit(1)
        write_thumbnail(files[0], thumb, config.tools)
        jsonout.set_extra(thumbnail=thumb)
    if sheet:
        write_contact_sheet(files, sheet, config.tools)
        jsonout.set_extra(contact_sheet=sheet)
    if failed:
        raise typer.Exit(1)


LABELS = {
    "kind": "Kind", "bytes": "File size", "format": "Format", "width": "Width", "height": "Height",
    "megapixels": "Megapixels", "colorspace": "Colour space", "colorspaces": "Colour spaces", "mode": "Pillow mode",
    "bit_depth": "Bits per channel", "dpi": "DPI", "alpha": "Has transparency (alpha)", "alpha_used": "Transparency used",
    "frames": "Frames", "animated": "Animated", "exif_orientation": "EXIF orientation", "upright_width": "Upright width",
    "upright_height": "Upright height", "icc_profile": "ICC profile", "dominant_colors": "Main colours",
    "sharpness": "Sharpness", "blurry": "Blurry", "blank": "Blank", "metadata": "Metadata", "viewbox": "viewBox",
    "colors": "Colours", "gradients": "Gradients", "has_text": "Live text", "fonts": "Fonts",
    "embedded_images": "Embedded images", "has_filters": "Filters", "external_references": "External references",
    "well_formed": "Well-formed XML", "warnings": "Warnings", "pages": "Pages", "page_size_points": "Page size (pt)",
    "output_intent": "Output intent", "spot_colours": "Spot colours", "note": "Note",
    "has_gps": "GPS position", "has_serial_number": "Serial number", "camera": "Camera", "software": "Software",
    "captured": "Captured", "ai_declaration": "AI declaration", "usage_terms": "Usage terms", "tag_count": "Tags",
}


def label(key: str) -> str:
    return LABELS.get(key) or key.replace("_", " ").capitalize()


def _value(key: str, value) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if value is None:
        return "-"
    if key == "bytes" and isinstance(value, int):
        return f"{value:,} B"
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return ", ".join(f"{v.get('hex')} {round(v['share'] * 100)}%" if "share" in v else f"{v.get('hex')} ×{v.get('uses', '')}"
                         for v in value)
    if isinstance(value, list):
        return ", ".join(str(v) for v in value) if value else "-"
    return str(value)


def _print(info: dict) -> None:
    table = Table(title=escape(info["path"]), show_header=False)
    for key, value in info.items():
        if key == "path":
            continue
        if isinstance(value, dict):
            if not value:
                continue
            for sub, v in value.items():
                table.add_row(escape(f"{label(key)} · {label(sub).lower()}"), escape(_value(sub, v)))
            continue
        table.add_row(escape(label(key)), escape(_value(key, value)))
    console.print(table)
