from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import typer

from mysuite.config import MysuiteConfigError, load_config
from mysuite.convert._parsing import InvalidInputError, resolve_input_files
from mysuite.cutout._parsing import output_path_for
from mysuite.cutout.cutout import CutoutError, cutout_file
from mysuite.doctor import missing_tools, run_doctor
from mysuite.utils.console import console, log_error, log_step
from mysuite.utils.subprocess_utils import MysuiteToolError


def cutout(
    inputs: List[Path] = typer.Argument(
        ..., exists=True, readable=True,
        help="One or more image files, and/or directories, to cut out (non-recursive unless --recursive).",
    ),
    recursive: bool = typer.Option(
        False, "--recursive", "-r", help="Recurse into subdirectories for folder inputs."
    ),
    overwrite: bool = typer.Option(
        False, "--overwrite/--no-overwrite", help="Overwrite existing output files."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Preview input -> output pairs without cutting out anything."
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
            output_path = output_path_for(input_path)
            if not quiet:
                console.print(f"[dim]{input_path} -> {output_path}[/dim]")
        if not quiet:
            console.print(f"\n[dim]dry run — {len(input_files)} cutout(s) planned, 0 written[/dim]")
        return

    total_written = 0
    total_skipped = 0
    failures: list[tuple[Path, str]] = []

    for input_path in input_files:
        try:
            outcome = cutout_file(input_path, tools=config.tools, overwrite=overwrite)
        except (CutoutError, MysuiteToolError) as exc:
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
