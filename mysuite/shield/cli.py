from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import typer
from rich.markup import escape

from mysuite import components
from mysuite.config import MysuiteConfigError, load_config
from mysuite.convert._parsing import InvalidInputError, RASTER_FORMATS, detect_source_format, resolve_input_files
from mysuite.shield.core import STRENGTHS, ShieldError, estimate_seconds, output_path_for, shield_file
from mysuite.utils import jsonout
from mysuite.utils.console import console, log_error, log_skip, log_step
from PIL import Image


@jsonout.with_json("shield")
def shield(
    inputs: List[Path] = typer.Argument(..., exists=True, readable=True, help="Images to protect (PNG/JPEG/WebP/TIFF)."),
    strength: str = typer.Option("standard", "--strength", help=f"{', '.join(STRENGTHS)}: stronger = more disruption, more visible pattern, slower."),
    steps: Optional[int] = typer.Option(None, "--steps", help="Optimisation steps (5-400); overrides the strength's default."),
    epsilon: Optional[int] = typer.Option(None, "--epsilon", help="Largest allowed change per pixel, out of 255 (1-32); overrides the strength's default."),
    device: Optional[str] = typer.Option(None, "--device", help="cpu, mps or cuda (default: the best available)."),
    seed: int = typer.Option(0, "--seed", help="Random start (same seed = same result)."),
    recursive: bool = typer.Option(False, "--recursive", "-r"),
    overwrite: bool = typer.Option(False, "--overwrite/--no-overwrite"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the files and a time estimate, change nothing."),
) -> None:
    """EXPERIMENTAL: make an image resist AI image editing (PhotoGuard-style). Read docs/SHIELD.md for what it does and does not do."""
    try:
        files = [f for f in resolve_input_files(inputs, recursive=recursive) if detect_source_format(f) in RASTER_FORMATS]
        if not files:
            raise InvalidInputError("no raster images found")
        if strength not in STRENGTHS:
            raise ShieldError(f"strength must be one of {', '.join(STRENGTHS)}")
    except (InvalidInputError, ShieldError) as exc:
        log_error(escape(str(exc)))
        raise typer.Exit(1) from exc
    it = steps or STRENGTHS[strength][1]
    if not dry_run and not components.get("shield").is_installed():
        jsonout.add_error("the shield component is not installed")
        jsonout.set_extra(missing_components=[{"component": "shield", "install": "mysuite install shield --yes"}])
        log_error("the shield component is not installed (PyTorch + weights, about 1.4 GB, fetched once): run `mysuite install shield`")
        raise typer.Exit(jsonout.EXIT_MISSING_TOOL)
    failed = 0
    for f in files:
        try:
            with Image.open(f) as im:
                w, h = im.size
        except Exception as exc:  # noqa: BLE001
            failed += 1
            jsonout.add_item(input=f, status="failed", error=f"can't read {f.name}: {exc}")
            log_error(f"{escape(str(f))}: can't read it")
            continue
        eta = estimate_seconds(w, h, it)
        if dry_run:
            jsonout.add_item(input=f, output=output_path_for(f), status="planned", estimated_seconds=eta, width=w, height=h)
            console.print(f"[dim]{escape(str(f))} -> {escape(str(output_path_for(f)))}  (about {eta // 60} min {eta % 60} s on a Mac GPU)[/dim]")
            continue
        console.print(f"[dim]{escape(f.name)}: {w}x{h}, about {eta // 60} min {eta % 60} s[/dim]")
        try:
            r = shield_file(f, strength=strength, steps=steps, epsilon=epsilon, device=device, seed=seed, overwrite=overwrite,
                            progress=lambda done, total: console.print(f"[dim]  tile {done}/{total}[/dim]"))
        except ShieldError as exc:
            failed += 1
            jsonout.add_item(input=f, status="failed", error=str(exc))
            log_error(f"{escape(str(f))}: {escape(str(exc))}")
            continue
        if r.status == "skipped_existing":
            jsonout.add_item(input=f, output=r.output_path, status="skipped_existing")
            console.print(f"[dim]— exists, skipped: {escape(str(r.output_path))}[/dim]")
            continue
        jsonout.add_item(input=f, output=r.output_path, status="written", metrics=r.metrics, notes=r.notes)
        log_step(f"{escape(str(r.output_path))}  [dim]{r.metrics.get('psnr_db')} dB, {r.metrics.get('seconds')} s on {r.metrics.get('device')}[/dim]")
        for n in r.notes:
            jsonout.add_warning(f"{f.name}: {n}")
            log_skip(escape(n))
    if failed:
        raise typer.Exit(1)
