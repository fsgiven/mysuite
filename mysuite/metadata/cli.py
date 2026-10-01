from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import typer

from rich.markup import escape
from mysuite.config import MysuiteConfigError, load_config
from mysuite.convert._parsing import InvalidInputError, resolve_input_files
from mysuite.doctor import missing_tools, run_doctor
from mysuite.metadata._parsing import output_path_for
from mysuite.metadata.metadata import credit_file, randomize_file, strip_file
from mysuite.utils.console import console, log_error, log_step
from mysuite.utils.subprocess_utils import MysuiteToolError

app = typer.Typer(
    help="Strip metadata for privacy, replace it with a plausible decoy camera identity, "
    "or embed a signed C2PA provenance record for correct crediting."
)


def _run_batch(input_files: list[Path], *, mode: str, run_one, dry_run: bool, quiet: bool) -> None:
    if dry_run:
        for input_path in input_files:
            output_path = output_path_for(input_path, mode)
            if not quiet:
                console.print(f"[dim]{escape(str(input_path))} -> {escape(str(output_path))}[/dim]")
        if not quiet:
            console.print(f"\n[dim]dry run — {len(input_files)} file(s) planned, 0 written[/dim]")
        return

    total_written = 0
    total_skipped = 0
    failures: list[tuple[Path, str]] = []

    for input_path in input_files:
        try:
            outcome = run_one(input_path)
        except MysuiteToolError as exc:
            failures.append((input_path, str(exc)))
            if not quiet:
                log_error(f"{escape(str(input_path))}: {escape(str(exc))}")
            continue

        if outcome.status == "skipped_existing":
            total_skipped += 1
            if not quiet:
                console.print(f"[dim]— exists, skipped: {escape(str(outcome.output_path))}[/dim]")
        else:
            total_written += 1
            if not quiet:
                log_step(escape(str(outcome.output_path)))

    if not quiet:
        console.print(
            f"\n[bold green]done[/bold green] — {total_written} written, "
            f"{total_skipped} already existed (use --overwrite to replace), "
            f"{len(failures)} failed"
        )

    if failures:
        raise typer.Exit(1)


@app.command("strip", help="Strip all EXIF/IPTC/XMP/ICC metadata, writing a new file beside the source.")
def strip(
    inputs: List[Path] = typer.Argument(
        ..., exists=True, readable=True,
        help="One or more image files, and/or directories (non-recursive unless --recursive).",
    ),
    recursive: bool = typer.Option(False, "--recursive", "-r", help="Recurse into subdirectories."),
    overwrite: bool = typer.Option(
        False, "--overwrite/--no-overwrite", help="Overwrite existing output files."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview input -> output pairs, write nothing."),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Explicit path to mysuite.toml."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress per-file progress, print summary only."),
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

    _run_batch(
        input_files, mode="stripped",
        run_one=lambda p: strip_file(p, tools=config.tools, overwrite=overwrite),
        dry_run=dry_run, quiet=quiet,
    )


@app.command("credit", help="Embed a signed C2PA provenance manifest (author/copyright) for correct crediting.")
def credit(
    inputs: List[Path] = typer.Argument(
        ..., exists=True, readable=True,
        help="One or more image files, and/or directories (non-recursive unless --recursive).",
    ),
    author: str = typer.Option(..., "--author", "-a", help="Author/creator name to embed."),
    copyright_notice: Optional[str] = typer.Option(
        None, "--copyright", help="Copyright notice to embed, e.g. '© 2026 Jane Doe'."
    ),
    generator: str = typer.Option(
        "mysuite", "--generator",
        help="Software/tool named as the claim generator (not independently visible in "
        "c2patool's own read-back report on all versions — author/copyright are the fields "
        "that reliably read back).",
    ),
    recursive: bool = typer.Option(False, "--recursive", "-r", help="Recurse into subdirectories."),
    overwrite: bool = typer.Option(
        False, "--overwrite/--no-overwrite", help="Overwrite existing output files."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview input -> output pairs, write nothing."),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Explicit path to mysuite.toml."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress per-file progress, print summary only."),
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

    _run_batch(
        input_files, mode="credited",
        run_one=lambda p: credit_file(
            p, author=author, copyright_notice=copyright_notice, generator=generator,
            tools=config.tools, overwrite=overwrite,
        ),
        dry_run=dry_run, quiet=quiet,
    )


@app.command(
    "randomize",
    help="Strip all metadata, then write one internally consistent decoy camera identity "
    "(make/model/lens/firmware + plausible exposure and a recent capture time; never GPS).",
)
def randomize(
    inputs: List[Path] = typer.Argument(
        ..., exists=True, readable=True,
        help="One or more image files, and/or directories (non-recursive unless --recursive).",
    ),
    recursive: bool = typer.Option(False, "--recursive", "-r", help="Recurse into subdirectories."),
    overwrite: bool = typer.Option(
        False, "--overwrite/--no-overwrite", help="Overwrite existing output files."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="List input -> output pairs, write nothing."),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Explicit path to mysuite.toml."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress per-file progress, print summary only."),
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

    _run_batch(
        input_files, mode="randomized",
        run_one=lambda p: randomize_file(p, tools=config.tools, overwrite=overwrite),
        dry_run=dry_run, quiet=quiet,
    )
