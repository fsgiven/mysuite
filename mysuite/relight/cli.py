from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import typer
from rich.markup import escape

from mysuite.convert._parsing import InvalidInputError, resolve_input_files
from mysuite.relight.relight import DIRECTIONS, PRESETS, RelightError, RelightSettings, output_path_for, relight_file
from mysuite.utils import jsonout
from mysuite.utils.console import console, log_error, log_skip, log_step

_RASTER = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp", ".gif"}


@jsonout.with_json("relight")
def relight(
    inputs: List[Path] = typer.Argument(..., exists=True, readable=True, help="Photos/images to relight (raster formats)."),
    preset: Optional[str] = typer.Option(None, "--preset", help=f"Lighting preset: {', '.join(PRESETS)}. Other flags override it."),
    direction: Optional[str] = typer.Option(None, "--direction", help=f"Where the light comes from: {', '.join(DIRECTIONS)}. Default top-left."),
    angle: Optional[float] = typer.Option(None, "--angle", help="Compass degrees the light comes FROM (0 top, 90 right, 180 bottom, 270 left); beats --direction."),
    height: Optional[float] = typer.Option(None, "--height", help="Light elevation in degrees, 1-90 (90 = straight on, low = raking). Default 40."),
    intensity: Optional[float] = typer.Option(None, "--intensity", help="How strongly the light shapes the picture, 0-3. Default 0.9."),
    ambient: Optional[float] = typer.Option(None, "--ambient", help="Base light everywhere, 0-2 (lower = deeper shadows). Default 0.55."),
    softness: Optional[float] = typer.Option(None, "--softness", help="Blur of the shape in percent of the short side, 0-50. Default 6."),
    depth: Optional[float] = typer.Option(None, "--depth", help="How pronounced the pseudo-3D is, 0-5. Default 1."),
    specular: Optional[float] = typer.Option(None, "--specular", help="Sheen, 0 = matte. Default 0."),
    color: Optional[str] = typer.Option(None, "--color", help="Tint of the light, e.g. '#ffb36b' (warm) or '#9ec5ff' (cool)."),
    recursive: bool = typer.Option(False, "--recursive", "-r", help="Recurse into subdirectories."),
    overwrite: bool = typer.Option(False, "--overwrite/--no-overwrite", help="Overwrite existing outputs."),
    dry_run: bool = typer.Option(False, "--dry-run", help="List input -> output pairs, write nothing."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Print only the summary."),
) -> None:
    """Experimental, classical relighting (no AI model): a lighting nudge for products, cut-outs and logos."""
    try:
        settings = RelightSettings.from_options(preset, {
            "direction": direction, "angle": angle, "height": height, "intensity": intensity, "ambient": ambient,
            "softness": softness, "depth": depth, "specular": specular, "color": color,
        })
        files = [f for f in resolve_input_files(inputs, recursive=recursive) if f.suffix.lower() in _RASTER]
        if not files:
            raise InvalidInputError("no raster images found (relight takes PNG/JPEG/WebP/TIFF/BMP/GIF)")
    except (RelightError, InvalidInputError) as exc:
        log_error(escape(str(exc)))
        raise typer.Exit(1) from exc

    written = skipped = failed = 0
    for f in files:
        out = output_path_for(f)
        if dry_run:
            jsonout.add_item(input=f, output=out, status="planned")
            if not quiet:
                console.print(f"[dim]{escape(str(f))} -> {escape(str(out))}[/dim]")
            continue
        try:
            outcome = relight_file(f, settings, overwrite=overwrite)
        except RelightError as exc:
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
        jsonout.add_item(input=f, output=outcome.output_path, status="written", size=list(outcome.size), notes=outcome.notes)
        for note in outcome.notes:
            log_skip(f"{escape(str(f))}: {escape(note)}")
        if not quiet:
            log_step(escape(str(outcome.output_path)))
    if dry_run:
        return
    jsonout.set_extra(settings={k: v for k, v in settings.__dict__.items()})
    if not quiet:
        console.print(f"\n[bold green]done[/bold green] — {written} written, {skipped} already existed "
                      f"(use --overwrite to replace), {failed} failed")
    if failed:
        raise typer.Exit(1)
