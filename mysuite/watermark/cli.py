from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import typer

from mysuite.config import MysuiteConfigError, load_config
from mysuite.convert._parsing import InvalidInputError, detect_source_format, resolve_input_files
from mysuite.doctor import missing_tools, run_doctor
from mysuite.utils.console import console, log_error, log_step
from mysuite.utils.subprocess_utils import MysuiteToolError
from mysuite.watermark._parsing import output_path_for
from mysuite.watermark.watermark import GRAVITY_BY_POSITION, WatermarkError, watermark_file


def watermark(
    inputs: List[Path] = typer.Argument(
        ..., exists=True, readable=True,
        help="One or more image files, and/or directories, to watermark (non-recursive unless --recursive).",
    ),
    logo: Path = typer.Option(
        ..., "--logo", "-l", exists=True, readable=True,
        help="Image (or SVG) to stamp onto each input as the watermark.",
    ),
    position: str = typer.Option(
        "bottom-right", "--position", "-p",
        help=f"One of: {', '.join(GRAVITY_BY_POSITION)}.",
    ),
    scale: float = typer.Option(
        15.0, "--scale", help="Logo width as a percentage of the base image's width."
    ),
    opacity: int = typer.Option(
        80, "--opacity", min=0, max=100, help="Watermark blend opacity, 0-100."
    ),
    margin: float = typer.Option(
        3.0, "--margin", help="Breathing room from the edge, as a percentage of the base image's width."
    ),
    recursive: bool = typer.Option(
        False, "--recursive", "-r", help="Recurse into subdirectories for folder inputs."
    ),
    overwrite: bool = typer.Option(
        False, "--overwrite/--no-overwrite", help="Overwrite existing output files."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Preview input -> output pairs without watermarking anything."
    ),
    config_path: Optional[Path] = typer.Option(
        None, "--config", "-c", help="Explicit path to mysuite.toml."
    ),
    quiet: bool = typer.Option(
        False, "--quiet", "-q", help="Suppress per-file progress, print summary only."
    ),
) -> None:
    if position not in GRAVITY_BY_POSITION:
        log_error(f"unknown --position {position!r} — expected one of {', '.join(GRAVITY_BY_POSITION)}")
        raise typer.Exit(1)

    try:
        input_files = resolve_input_files(inputs, recursive=recursive)
    except InvalidInputError as exc:
        log_error(str(exc))
        raise typer.Exit(1) from exc

    if detect_source_format(logo) is None:
        log_error(f"unrecognized logo format: {logo}")
        raise typer.Exit(1)

    try:
        config = load_config(config_path)
    except MysuiteConfigError as exc:
        log_error(str(exc))
        raise typer.Exit(1) from exc

    missing = missing_tools(config.tools)
    if missing:
        run_doctor(config.tools)
        raise typer.Exit(1)

    if dry_run:
        for input_path in input_files:
            source_format = detect_source_format(input_path)
            if source_format is None:
                continue
            output_path = output_path_for(input_path, source_format)
            if not quiet:
                console.print(f"[dim]{input_path} -> {output_path}[/dim]")
        if not quiet:
            console.print(f"\n[dim]dry run — {len(input_files)} watermark(s) planned, 0 written[/dim]")
        return

    total_written = 0
    total_skipped = 0
    failures: list[tuple[Path, str]] = []

    for input_path in input_files:
        try:
            outcome = watermark_file(
                input_path, logo_path=logo, tools=config.tools,
                position=position, scale_pct=scale, opacity=opacity, margin_pct=margin,
                overwrite=overwrite,
            )
        except (WatermarkError, MysuiteToolError) as exc:
            failures.append((input_path, str(exc)))
            if not quiet:
                log_error(f"{input_path}: {exc}")
            continue

        if outcome.status == "skipped_existing":
            total_skipped += 1
            if not quiet:
                console.print(f"[dim]— exists, skipped: {outcome.output_path}[/dim]")
        else:
            total_written += 1
            if not quiet:
                log_step(str(outcome.output_path))

    if not quiet:
        console.print(
            f"\n[bold green]done[/bold green] — {total_written} written, "
            f"{total_skipped} already existed (use --overwrite to replace), "
            f"{len(failures)} failed"
        )

    if failures:
        raise typer.Exit(1)
