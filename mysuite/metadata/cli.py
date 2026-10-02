from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import typer

from rich.markup import escape
from mysuite import profiles as profiles_mod
from mysuite.config import MysuiteConfigError, load_config
from mysuite.profiles import ProfileError
from mysuite.convert._parsing import InvalidInputError, resolve_input_files
from mysuite.doctor import require_tools
from mysuite.metadata._parsing import output_path_for
from mysuite.metadata.metadata import DATA_MINING, credit_file, declare_file, randomize_file, strip_file
from mysuite.utils import jsonout
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
            jsonout.add_item(input=input_path, output=output_path, status="planned")
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
            jsonout.add_item(input=input_path, status="failed", error=str(exc))
            if not quiet:
                log_error(f"{escape(str(input_path))}: {escape(str(exc))}")
            continue

        jsonout.add_item(input=input_path, output=outcome.output_path,
                         status="skipped_existing" if outcome.status == "skipped_existing" else "written")
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
@jsonout.with_json("metadata-strip")
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

    require_tools(config.tools, "metadata-strip")

    _run_batch(
        input_files, mode="stripped",
        run_one=lambda p: strip_file(p, tools=config.tools, overwrite=overwrite),
        dry_run=dry_run, quiet=quiet,
    )


@app.command("credit", help="Embed a signed C2PA provenance manifest (author/copyright) for correct crediting.")
@jsonout.with_json("metadata-credit")
def credit(
    inputs: List[Path] = typer.Argument(
        ..., exists=True, readable=True,
        help="One or more image files, and/or directories (non-recursive unless --recursive).",
    ),
    author: Optional[str] = typer.Option(None, "--author", "-a", help="Author/creator name to embed (or the active profile's metadata.author)."),
    copyright_notice: Optional[str] = typer.Option(
        None, "--copyright", help="Copyright notice to embed, e.g. '© 2026 Jane Doe'."
    ),
    no_ai: bool = typer.Option(False, "--no-ai", help="Also record in the manifest that AI training, generative training, inference and data mining are NOT allowed (C2PA training-mining assertion)."),
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

    try:
        active_profile = profiles_mod.resolve(config)
    except ProfileError as exc:
        log_error(escape(str(exc)))
        raise typer.Exit(1) from exc
    if active_profile:
        author = author or active_profile.metadata.get("author")
        copyright_notice = copyright_notice or active_profile.metadata.get("copyright")
    if not author:
        log_error("an author is needed: pass --author or set metadata.author in the active profile")
        raise typer.Exit(1)
    require_tools(config.tools, "metadata-credit")

    _run_batch(
        input_files, mode="credited",
        run_one=lambda p: credit_file(
            p, author=author, copyright_notice=copyright_notice, generator=generator,
            tools=config.tools, overwrite=overwrite, no_ai=no_ai or bool(active_profile and active_profile.metadata.get("no_ai")),
        ),
        dry_run=dry_run, quiet=quiet,
    )


@app.command(
    "randomize",
    help="Strip all metadata, then write one internally consistent decoy camera identity "
    "(make/model/lens/firmware + plausible exposure and a recent capture time; never GPS).",
)
@jsonout.with_json("metadata-randomize")
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

    require_tools(config.tools, "metadata-randomize")

    _run_batch(
        input_files, mode="randomized",
        run_one=lambda p: randomize_file(p, tools=config.tools, overwrite=overwrite),
        dry_run=dry_run, quiet=quiet,
    )


@app.command(
    "apply",
    help="Apply a metadata policy in one go - the active profile's, or --policy: any of strip, randomize, credit, in order "
    "(e.g. strip,credit removes everything, then embeds your credit). Each step works on the previous step's output.",
)
@jsonout.with_json("metadata-apply")
def apply(
    inputs: List[Path] = typer.Argument(..., exists=True, readable=True, help="Image files and/or folders."),
    policy: Optional[str] = typer.Option(None, "--policy", help="Comma-separated steps: strip, randomize, credit (default: the profile's metadata.policy)."),
    author: Optional[str] = typer.Option(None, "--author", "-a", help="For the credit step (default: the profile's metadata.author)."),
    copyright_notice: Optional[str] = typer.Option(None, "--copyright", help="For the credit step (default: the profile's metadata.copyright)."),
    recursive: bool = typer.Option(False, "--recursive", "-r"),
    overwrite: bool = typer.Option(False, "--overwrite/--no-overwrite"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview the final file names, write nothing."),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Explicit path to mysuite.toml."),
    quiet: bool = typer.Option(False, "--quiet", "-q"),
) -> None:
    from mysuite.profiles import POLICIES

    try:
        input_files = resolve_input_files(inputs, recursive=recursive)
        config = load_config(config_path)
        active_profile = profiles_mod.resolve(config)
    except (InvalidInputError, MysuiteConfigError, ProfileError) as exc:
        log_error(escape(str(exc)))
        raise typer.Exit(1) from exc
    steps = [s.strip() for s in policy.split(",") if s.strip()] if policy else (active_profile.policy if active_profile else [])
    if not steps or any(s not in POLICIES for s in steps):
        log_error("give --policy with steps from: " + ", ".join(POLICIES) + " (or select a profile that has metadata.policy)")
        raise typer.Exit(1)
    author = author or (active_profile.metadata.get("author") if active_profile else None)
    copyright_notice = copyright_notice or (active_profile.metadata.get("copyright") if active_profile else None)
    if "credit" in steps and not author:
        log_error("the credit step needs an author: --author, or metadata.author in the profile")
        raise typer.Exit(1)
    for step in steps:
        require_tools(config.tools, f"metadata-{step}")
    jsonout.set_extra(policy=steps, profile=active_profile.name if active_profile else None)
    modes = {"strip": "stripped", "randomize": "randomized", "credit": "credited", "declare": "declared"}
    profile_meta = active_profile.metadata if active_profile else {}
    runners = {
        "strip": lambda p: strip_file(p, tools=config.tools, overwrite=overwrite),
        "randomize": lambda p: randomize_file(p, tools=config.tools, overwrite=overwrite),
        "credit": lambda p: credit_file(p, author=author, copyright_notice=copyright_notice, generator="mysuite", tools=config.tools,
                                        overwrite=overwrite, no_ai=bool(profile_meta.get("no_ai"))),
        "declare": lambda p: declare_file(p, policy="prohibited", owner=author, terms_url=profile_meta.get("terms_url"),
                                          tools=config.tools, overwrite=overwrite),
    }
    failed = 0
    for src in input_files:
        current = src
        try:
            for step in steps:
                if dry_run:
                    current = output_path_for(current, modes[step])
                    continue
                outcome = runners[step](current)
                current = outcome.output_path
        except MysuiteToolError as exc:
            failed += 1
            jsonout.add_item(input=src, status="failed", step=step, error=str(exc))
            log_error(f"{escape(str(src))} ({step}): {escape(str(exc))}")
            continue
        jsonout.add_item(input=src, output=current, steps=steps, status="planned" if dry_run else "written")
        if not quiet:
            console.print(f"[dim]{escape(str(src))} -> {escape(str(current))}[/dim]" if dry_run else "")
            if not dry_run:
                log_step(escape(str(current)))
    if failed:
        raise typer.Exit(1)


@app.command(
    "declare",
    help="Write a machine-readable 'no AI use' declaration (PLUS DataMining + XMP Rights) into a copy (name_declared.ext). "
    "It is a legal/consent signal that cooperating crawlers and tools read - it does not technically block anything.",
)
@jsonout.with_json("metadata-declare")
def declare(
    inputs: List[Path] = typer.Argument(..., exists=True, readable=True, help="Image files and/or folders."),
    policy: str = typer.Option("prohibited", "--policy", help=f"{', '.join(DATA_MINING)}: what is not allowed."),
    owner: Optional[str] = typer.Option(None, "--owner", help="Copyright owner name to record."),
    terms_url: Optional[str] = typer.Option(None, "--terms-url", help="Link to your AI-use terms."),
    recursive: bool = typer.Option(False, "--recursive", "-r"),
    overwrite: bool = typer.Option(False, "--overwrite/--no-overwrite"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Explicit path to mysuite.toml."),
    quiet: bool = typer.Option(False, "--quiet", "-q"),
) -> None:
    if policy not in DATA_MINING:
        log_error(f"policy must be one of {', '.join(DATA_MINING)}")
        raise typer.Exit(1)
    try:
        input_files = resolve_input_files(inputs, recursive=recursive)
        config = load_config(config_path)
    except (InvalidInputError, MysuiteConfigError) as exc:
        log_error(escape(str(exc)))
        raise typer.Exit(1) from exc
    require_tools(config.tools, "metadata-declare")
    _run_batch(
        input_files, mode="declared",
        run_one=lambda p: declare_file(p, policy=policy, owner=owner, terms_url=terms_url, tools=config.tools, overwrite=overwrite),
        dry_run=dry_run, quiet=quiet,
    )
