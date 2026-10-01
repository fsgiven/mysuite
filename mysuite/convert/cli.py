from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import typer

from rich.markup import escape
from mysuite.config import MysuiteConfigError, load_config
from mysuite.convert._parsing import (
    TARGET_EXTENSIONS,
    InvalidInputError,
    check_no_output_collisions,
    output_path_for,
    resolve_input_files,
)
from mysuite.convert.converter import ConversionError, convert_file
from mysuite.doctor import require_tools
from mysuite.export._parsing import parse_csv
from mysuite.utils import jsonout
from mysuite.utils.console import console, log_error, log_skip, log_step
from mysuite.utils.subprocess_utils import MysuiteToolError


@jsonout.with_json("convert")
def convert(
    inputs: List[Path] = typer.Argument(
        ..., exists=True, readable=True,
        help="One or more files, and/or directories, to convert (non-recursive unless --recursive).",
    ),
    to: str = typer.Option(
        ..., "--to", "-t",
        help="Comma-separated target format(s), e.g. webp or png,pdf.",
    ),
    quality: Optional[int] = typer.Option(
        None, "--quality", min=0, max=100, help="JPEG/WebP quality, 0-100."
    ),
    dpi: float = typer.Option(
        300, "--dpi", "--ppi",
        help="Resolution used only when rasterizing a vector source (svg/pdf/eps); ignored for raster-to-raster conversions.",
    ),
    background: Optional[str] = typer.Option(
        None, "--background",
        help="Flatten transparency onto a color (jpeg/bmp default to white if unset — they have no alpha channel).",
    ),
    recursive: bool = typer.Option(
        False, "--recursive", "-r", help="Recurse into subdirectories for folder inputs."
    ),
    overwrite: bool = typer.Option(
        False, "--overwrite/--no-overwrite", help="Overwrite existing output files."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Preview input -> output pairs without converting."
    ),
    config_path: Optional[Path] = typer.Option(
        None, "--config", "-c", help="Explicit path to mysuite.toml."
    ),
    quiet: bool = typer.Option(
        False, "--quiet", "-q", help="Suppress per-file progress, print summary only."
    ),
) -> None:
    try:
        input_files = resolve_input_files(inputs, recursive=recursive)
    except InvalidInputError as exc:
        log_error(str(exc))
        raise typer.Exit(1) from exc

    target_formats = parse_csv(to) or []
    unknown = set(target_formats) - set(TARGET_EXTENSIONS)
    if unknown:
        log_error(
            f"unknown target format(s): {', '.join(sorted(unknown))} — "
            f"expected one of {', '.join(sorted(TARGET_EXTENSIONS))}"
        )
        raise typer.Exit(1)

    try:
        config = load_config(config_path)
    except MysuiteConfigError as exc:
        log_error(str(exc))
        raise typer.Exit(1) from exc

    require_tools(config.tools, "convert")

    for target_format in target_formats:
        try:
            check_no_output_collisions(input_files, target_format)
        except InvalidInputError as exc:
            log_error(str(exc))
            raise typer.Exit(1) from exc

    if dry_run:
        for input_path in input_files:
            for target_format in target_formats:
                output_path = output_path_for(input_path, target_format)
                jsonout.add_item(input=input_path, output=output_path, format=target_format, status="planned")
                if not quiet:
                    console.print(f"[dim]{escape(str(input_path))} -> {escape(str(output_path))}[/dim]")
        total_planned = len(input_files) * len(target_formats)
        if not quiet:
            console.print(f"\n[dim]dry run — {total_planned} conversion(s) planned, 0 written[/dim]")
        return

    total_written = 0
    total_skipped = 0
    failures: list[tuple[Path, str, str]] = []

    for input_path in input_files:
        for target_format in target_formats:
            try:
                outcome = convert_file(
                    input_path, target_format,
                    tools=config.tools, dpi=dpi, quality=quality,
                    background=background, overwrite=overwrite,
                )
            except (ConversionError, MysuiteToolError) as exc:
                failures.append((input_path, target_format, str(exc)))
                jsonout.add_item(input=input_path, format=target_format, status="failed", error=str(exc))
                if not quiet:
                    log_error(f"{escape(str(input_path))} -> {target_format}: {escape(str(exc))}")
                continue

            jsonout.add_item(
                input=input_path, output=outcome.output_path, format=target_format,
                status="skipped_existing" if outcome.status == "skipped_existing" else "written",
                note=outcome.note,
            )
            if outcome.note:
                jsonout.add_warning(f"{input_path}: {outcome.note}")
            if outcome.status == "skipped_existing":
                total_skipped += 1
                if not quiet:
                    console.print(f"[dim]— exists, skipped: {escape(str(outcome.output_path))}[/dim]")
            else:
                total_written += 1
                if not quiet:
                    log_step(escape(str(outcome.output_path)))
                if outcome.note:  # shown even with --quiet: it means data was left out
                    log_skip(f"{escape(str(input_path))}: {escape(outcome.note)}")

    if not quiet:
        console.print(
            f"\n[bold green]done[/bold green] — {total_written} written, "
            f"{total_skipped} already existed (use --overwrite to replace), "
            f"{len(failures)} failed"
        )

    if failures:
        raise typer.Exit(1)
