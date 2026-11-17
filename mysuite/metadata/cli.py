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
from mysuite.metadata.hidden import KEPT, REMOVED, is_own_output
from mysuite.metadata.metadata import DATA_MINING, credit_file, declare_file, randomize_file, strip_file
from mysuite.utils import jsonout
from mysuite.utils.console import console, log_error, log_note, log_step
from mysuite.utils.subprocess_utils import MysuiteToolError

app = typer.Typer(
    help="Strip metadata for privacy, replace it with a plausible decoy camera identity, "
    "or embed a signed C2PA provenance record for correct crediting."
)


def _resolve(inputs: list[Path], recursive: bool) -> list[Path]:
    """Files to work on. In a folder, mysuite's own earlier outputs (photo_stripped.jpg, photo_marked.png …) are skipped,
    with a note, so a second run doesn't process last run's results. A file named explicitly is always used."""
    files = resolve_input_files(inputs, recursive=recursive)
    named = {Path(i).resolve() for i in inputs if Path(i).is_file()}
    skipped = [f for f in files if is_own_output(f) and f.resolve() not in named]
    if skipped:
        names = ", ".join(f.name for f in skipped[:3]) + (f" and {len(skipped) - 3} more" if len(skipped) > 3 else "")
        msg = f"skipped {len(skipped)} file(s) mysuite wrote earlier: {names} (name one explicitly to process it)"
        jsonout.add_warning(msg)
        log_note(escape(msg))
    files = [f for f in files if f not in skipped]
    if not files:
        raise InvalidInputError("nothing to do: the folder only holds files mysuite wrote earlier")
    return files


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
        except (MysuiteToolError, ValueError) as exc:       # ValueError: PdfError from the PDF engine
            failures.append((input_path, str(exc)))
            jsonout.add_item(input=input_path, status="failed", error=str(exc))
            if not quiet:
                log_error(f"{escape(str(input_path))}: {escape(str(exc))}")
            continue

        jsonout.add_item(input=input_path, output=outcome.output_path,
                         status="skipped_existing" if outcome.status == "skipped_existing" else "written",
                         **getattr(outcome, "extra", {}))
        for note in getattr(outcome, "notes", []):
            jsonout.add_warning(f"{input_path.name}: {note}")
            if not quiet:
                log_note(escape(f"{input_path.name}: {note}"))
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


@app.command("strip", help=f"Remove hidden data ({REMOVED}) into a copy, name_stripped.ext. Keeps {KEPT}. "
             "--keep-credit keeps author, copyright and credit line. PDFs: title, author, dates and XMP go (as pdf strip). "
             "In a folder, files mysuite wrote earlier (_stripped, _marked …) are skipped.")
@jsonout.with_json("metadata-strip")
def strip(
    inputs: List[Path] = typer.Argument(
        ..., exists=True, readable=True,
        help="One or more image files, and/or directories (non-recursive unless --recursive).",
    ),
    keep_credit: bool = typer.Option(False, "--keep-credit", help="Keep author, copyright, credit line and source (EXIF, IPTC, XMP); "
                                     "still remove GPS, camera, serial numbers, software and comments."),
    drop_icc: bool = typer.Option(False, "--drop-icc", help="Also remove the colour profile (colours may then look different)."),
    recursive: bool = typer.Option(False, "--recursive", "-r", help="Recurse into subdirectories."),
    overwrite: bool = typer.Option(
        False, "--overwrite/--no-overwrite", help="Overwrite existing output files."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview input -> output pairs, write nothing."),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Explicit path to mysuite.toml."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress per-file progress, print summary only."),
) -> None:
    try:
        input_files = _resolve(inputs, recursive)
    except InvalidInputError as exc:
        log_error(str(exc))
        raise typer.Exit(1) from exc

    try:
        config = load_config(config_path)
    except MysuiteConfigError as exc:
        log_error(str(exc))
        raise typer.Exit(1) from exc

    if any(f.suffix.lower() != ".pdf" for f in input_files):
        require_tools(config.tools, "metadata-strip")
    if (keep_credit or drop_icc) and any(f.suffix.lower() == ".pdf" for f in input_files):
        jsonout.add_warning("--keep-credit and --drop-icc apply to pictures; PDFs lose title, author, dates and XMP")

    _run_batch(
        input_files, mode="stripped",
        run_one=lambda p: strip_file(p, tools=config.tools, overwrite=overwrite, keep_icc=not drop_icc, keep_credit=keep_credit),
        dry_run=dry_run, quiet=quiet,
    )


@app.command("credit", help="Write your credit into a copy (name_credited.ext): author (IPTC By-line, dc:creator, Artist), copyright "
             "notice, credit line, source and usage terms as IPTC Core via exiftool, which picture desks and DAMs read. When "
             "c2patool is installed a C2PA record with the same author/copyright is added last, signed with c2patool's TEST "
             "certificate (verifiers show it as untrusted); without c2patool the result says C2PA was not added.")
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
    credit_line: Optional[str] = typer.Option(None, "--credit-line", help="Credit line as it should be printed, e.g. 'Jane Doe / Acme News'."),
    source: Optional[str] = typer.Option(None, "--source", help="Source: the agency or archive that supplies the picture."),
    usage_terms: Optional[str] = typer.Option(None, "--usage-terms", help="Rights usage terms, e.g. 'Editorial use only'."),
    no_ai: bool = typer.Option(False, "--no-ai", help="Also declare that AI training, generative training and data mining are NOT allowed "
                               "(XMP PLUS DataMining, and the C2PA training-mining assertion when c2patool is installed)."),
    c2pa: bool = typer.Option(True, "--c2pa/--no-c2pa", help="Add a C2PA record when c2patool is installed (default) or never."),
    generator: str = typer.Option(
        "mysuite", "--generator",
        help="Software named as the C2PA claim generator (c2patool's read-back report may show its own name instead).",
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
        input_files = _resolve(inputs, recursive)
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
    require_tools(config.tools, "metadata-declare")          # exiftool; c2patool is optional

    _run_batch(
        input_files, mode="credited",
        run_one=lambda p: credit_file(
            p, author=author, copyright_notice=copyright_notice, credit_line=credit_line, source=source,
            usage_terms=usage_terms, generator=generator, tools=config.tools, overwrite=overwrite, c2pa=c2pa,
            no_ai=no_ai or bool(active_profile and active_profile.metadata.get("no_ai")),
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
        input_files = _resolve(inputs, recursive)
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
        input_files = _resolve(inputs, recursive)
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
    "It is a legal/consent signal that cooperating crawlers and tools read - it does not technically block anything. "
    "Nothing else is removed: a GPS location stays (a warning says so); `metadata protect` strips it in the same go.",
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
        input_files = _resolve(inputs, recursive)
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
