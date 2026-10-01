from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import typer
from rich.markup import escape

from mysuite.config import MysuiteConfigError, load_config
from mysuite.convert._parsing import InvalidInputError, resolve_input_files
from mysuite.transform.transform import Ops, TransformError, output_path_for, transform_file
from mysuite.utils import jsonout
from mysuite.utils.console import console, log_error, log_skip, log_step
from mysuite.convert._parsing import detect_source_format
from mysuite.utils.subprocess_utils import MysuiteToolError


@jsonout.with_json("transform")
def transform(
    inputs: List[Path] = typer.Argument(..., exists=True, readable=True, help="Files and/or folders to edit."),
    trim: bool = typer.Option(False, "--trim", help="Cut away a uniform border (transparent, else the top-left colour)."),
    trim_fuzz: float = typer.Option(0.0, "--trim-fuzz", min=0, max=100, help="How much a pixel may differ from the border colour, in percent."),
    crop: Optional[str] = typer.Option(None, "--crop", help="Crop box WIDTHxHEIGHT+X+Y in pixels, e.g. 800x600+100+50."),
    crop_aspect: Optional[str] = typer.Option(None, "--crop-aspect", help="Crop to an aspect ratio like 16:9 or 1:1, keeping as much as possible (see --gravity)."),
    gravity: str = typer.Option("center", "--gravity", help="Which part to keep/anchor: center, top, bottom, left, right, top-left, top-right, bottom-left, bottom-right."),
    rotate: Optional[float] = typer.Option(None, "--rotate", help="Rotate clockwise by this many degrees (90/180/270 are exact)."),
    flip: Optional[str] = typer.Option(None, "--flip", help="Mirror: horizontal, vertical or both."),
    resize: Optional[str] = typer.Option(None, "--resize", help="512 (width), x512 (height), 512x512 (box) or 50%. See --resize-mode."),
    resize_mode: str = typer.Option("fit", "--resize-mode", help="fit = inside the box keeping aspect; fill = cover the box and crop; exact = stretch."),
    shrink_only: bool = typer.Option(False, "--shrink-only", help="Never enlarge: leave images already smaller than the target alone."),
    pad: Optional[str] = typer.Option(None, "--pad", help="Extend the canvas to an aspect (1:1) or size (1200x630) without cropping."),
    pad_color: Optional[str] = typer.Option(None, "--pad-color", help="Padding colour (default transparent; white for JPEG)."),
    round_corners: Optional[str] = typer.Option(None, "--round", help="Round the corners: radius in px (24) or percent (50% on a square = circle)."),
    background: Optional[str] = typer.Option(None, "--background", help="Flatten transparency onto this colour."),
    output_format: Optional[str] = typer.Option(None, "--format", help="png, jpeg, webp or tiff (default: same as the source; vectors become PNG)."),
    quality: Optional[int] = typer.Option(None, "--quality", min=1, max=100, help="JPEG/WebP quality."),
    suffix: str = typer.Option("_transformed", "--suffix", help="Added to the file name; the original is never modified."),
    no_auto_orient: bool = typer.Option(False, "--no-auto-orient", help="Do not apply the EXIF rotation first."),
    dpi: float = typer.Option(300.0, "--dpi", help="Rasterisation density for SVG/PDF/EPS sources."),
    recursive: bool = typer.Option(False, "--recursive", "-r", help="Recurse into subdirectories."),
    overwrite: bool = typer.Option(False, "--overwrite/--no-overwrite", help="Overwrite existing outputs."),
    dry_run: bool = typer.Option(False, "--dry-run", help="List input -> output pairs, write nothing."),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Explicit path to mysuite.toml."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Print only the summary."),
) -> None:
    ops = Ops(
        auto_orient=not no_auto_orient, trim=trim, trim_fuzz=trim_fuzz, crop=crop, crop_aspect=crop_aspect,
        gravity=gravity, rotate=rotate, flip=flip, resize=resize, resize_mode=resize_mode, shrink_only=shrink_only,
        pad=pad, pad_color=pad_color, round=round_corners, background=background, format=output_format, quality=quality,
    )
    try:
        ops.validate()
        if not (ops.active() or ops.format):
            raise TransformError("nothing to do: give at least one of --trim --crop --crop-aspect --rotate --flip --resize --pad --round --background --format")
        files = resolve_input_files(inputs, recursive=recursive)
        config = load_config(config_path)
    except (TransformError, InvalidInputError, MysuiteConfigError) as exc:
        log_error(escape(str(exc)))
        raise typer.Exit(1) from exc

    if dry_run:
        for f in files:
            out = output_path_for(f, ops, suffix, detect_source_format(f) or "png")
            jsonout.add_item(input=f, output=out, status="planned", applied=ops.active())
            if not quiet:
                console.print(f"[dim]{escape(str(f))} -> {escape(str(out))}[/dim]")
        return

    written = skipped = failed = 0
    for f in files:
        try:
            outcome = transform_file(f, ops, tools=config.tools, suffix=suffix, overwrite=overwrite, dpi=dpi)
        except (TransformError, MysuiteToolError) as exc:
            failed += 1
            jsonout.add_item(input=f, status="failed", error=str(exc))
            log_error(f"{escape(str(f))}: {escape(str(exc))}")
            continue
        if outcome.status == "skipped_existing":
            skipped += 1
            jsonout.add_item(input=f, output=outcome.output_path, status="skipped_existing")
            if not quiet:
                console.print(f"[dim]— exists, skipped: {escape(str(outcome.output_path))}[/dim]")
            continue
        written += 1
        jsonout.add_item(input=f, output=outcome.output_path, status="written", applied=outcome.applied,
                         input_size=list(outcome.input_size), output_size=list(outcome.output_size), notes=outcome.notes)
        for note in outcome.notes:
            log_skip(f"{escape(str(f))}: {escape(note)}")
        if not quiet:
            log_step(f"{escape(str(outcome.output_path))}  [dim]{outcome.input_size[0]}x{outcome.input_size[1]} -> "
                     f"{outcome.output_size[0]}x{outcome.output_size[1]}[/dim]")
    if not quiet:
        console.print(f"\n[bold green]done[/bold green] — {written} written, {skipped} already existed "
                      f"(use --overwrite to replace), {failed} failed")
    if failed:
        raise typer.Exit(1)
