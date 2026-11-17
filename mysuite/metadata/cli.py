from __future__ import annotations

import os

from pathlib import Path
from typing import List, Literal, Optional

import typer

from rich.markup import escape
from mysuite import profiles as profiles_mod
from mysuite.config import MysuiteConfigError, load_config
from mysuite.profiles import ProfileError
from mysuite.convert._parsing import InvalidInputError, resolve_input_files
from mysuite.doctor import require_tools
from mysuite.metadata._parsing import output_path_for
from mysuite.metadata.hidden import KEPT, REMOVED, is_own_output
from mysuite.metadata.metadata import (
    DATA_MINING, ChainOptions, chain_output_path, credit_file, declare_file, expand_steps, randomize_file, run_chain, strip_file,
)
from mysuite.utils import jsonout
from mysuite.utils.console import console, log_error, log_note, log_step
from mysuite.utils.subprocess_utils import MysuiteToolError

RANDOMIZE_REFUSED = ("randomize writes a made-up camera and date, which is false provenance for published pictures; "
                     "it runs only with --advanced (use `metadata strip` to remove the real ones)")

app = typer.Typer(
    help="Hidden data in pictures: strip it for privacy (GPS, camera, serials; the colour profile stays), credit the author "
    "(IPTC Core, plus C2PA when c2patool is installed), declare 'no AI use', or protect: all of it in one go. Copies only."
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
    hidden=True,
    help="ADVANCED, hidden: strip all metadata, then write one internally consistent decoy camera identity "
    "(make/model/lens/firmware + plausible exposure and a recent capture time; never GPS). A made-up camera and date is "
    "false provenance for published pictures, so it runs only with --advanced.",
)
@jsonout.with_json("metadata-randomize")
def randomize(
    inputs: List[Path] = typer.Argument(
        ..., exists=True, readable=True,
        help="One or more image files, and/or directories (non-recursive unless --recursive).",
    ),
    advanced: bool = typer.Option(False, "--advanced", help="Required: you know the result carries a made-up camera and date."),
    recursive: bool = typer.Option(False, "--recursive", "-r", help="Recurse into subdirectories."),
    overwrite: bool = typer.Option(
        False, "--overwrite/--no-overwrite", help="Overwrite existing output files."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="List input -> output pairs, write nothing."),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Explicit path to mysuite.toml."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress per-file progress, print summary only."),
) -> None:
    if not advanced:
        log_error(RANDOMIZE_REFUSED)
        raise typer.Exit(jsonout.EXIT_USAGE)
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


def _chain_batch(input_files: list[Path], requested: list[str], steps: list[str], *, opts, config, overwrite: bool,
                 dry_run: bool, quiet: bool) -> None:
    failed = 0
    for src in input_files:
        target = chain_output_path(src, steps, requested)
        if dry_run:
            jsonout.add_item(input=src, output=target, steps=steps, status="planned")
            if not quiet:
                console.print(f"[dim]{escape(str(src))} -> {escape(str(target))}[/dim]")
            continue
        try:
            outcome = run_chain(src, steps, output_path=target, tools=config.tools, opts=opts, overwrite=overwrite)
        except (MysuiteToolError, ValueError) as exc:
            failed += 1
            jsonout.add_item(input=src, status="failed", steps=steps, error=str(exc))
            log_error(f"{escape(str(src))}: {escape(str(exc))}")
            continue
        jsonout.add_item(input=src, output=outcome.output_path, status=outcome.status, **{"steps": steps, **outcome.extra})
        for note in outcome.notes:
            jsonout.add_warning(f"{src.name}: {note}")
            if not quiet:
                log_note(escape(f"{src.name}: {note}"))
        if quiet:
            continue
        if outcome.status == "skipped_existing":
            console.print(f"[dim]— exists, skipped: {escape(str(outcome.output_path))}[/dim]")
        else:
            log_step(escape(str(outcome.output_path)))
    if failed:
        raise typer.Exit(1)


def _mark_key(key: Optional[str]) -> str:
    from mysuite.mark import core as mark_core

    key = key or os.environ.get("MYSUITE_MARK_KEY")
    if not key:
        log_error("the mark step needs your secret key: --key SECRET or the MYSUITE_MARK_KEY environment variable")
        raise typer.Exit(jsonout.EXIT_USAGE)
    try:
        return mark_core.check_key(key)
    except mark_core.MarkError as exc:
        log_error(escape(str(exc)))
        raise typer.Exit(jsonout.EXIT_USAGE) from exc


@app.command(
    "protect",
    help="Protect & credit in one go, into ONE copy (name_protected.ext): remove private data (GPS, camera, serial numbers, "
    "software; your existing credit and the colour profile stay), write your credit (IPTC Core), declare 'no AI training / data "
    "mining', and with --mark add your invisible ownership mark. A C2PA record is added last when c2patool is installed (test "
    "certificate). No intermediate files are left behind.",
)
@jsonout.with_json("metadata-protect")
def protect(
    inputs: List[Path] = typer.Argument(..., exists=True, readable=True, help="Image files and/or folders."),
    author: Optional[str] = typer.Option(None, "--author", "-a", help="Author/creator (or the profile's metadata.author)."),
    copyright_notice: Optional[str] = typer.Option(None, "--copyright", help="Copyright notice, e.g. '© 2026 Jane Doe' (or the profile's)."),
    credit_line: Optional[str] = typer.Option(None, "--credit-line", help="Credit line as it should be printed."),
    source: Optional[str] = typer.Option(None, "--source", help="Source: the agency or archive."),
    usage_terms: Optional[str] = typer.Option(None, "--usage-terms", help="Rights usage terms, e.g. 'Editorial use only'."),
    policy: Literal["prohibited", "prohibited-ai-training", "prohibited-genai-training"] = typer.Option(
        "prohibited", "--policy", help="What the no-AI declaration forbids."),
    terms_url: Optional[str] = typer.Option(None, "--terms-url", help="Link to your AI-use terms (or the profile's)."),
    mark: bool = typer.Option(False, "--mark", help="Also add the invisible ownership mark (needs --key or MYSUITE_MARK_KEY)."),
    key: Optional[str] = typer.Option(None, "--key", help="Secret key for --mark (better: the MYSUITE_MARK_KEY environment variable)."),
    ident: Optional[str] = typer.Option(None, "--id", help="Optional label mixed into the mark."),
    c2pa: bool = typer.Option(True, "--c2pa/--no-c2pa", help="Add a C2PA record when c2patool is installed (default) or never."),
    recursive: bool = typer.Option(False, "--recursive", "-r"),
    overwrite: bool = typer.Option(False, "--overwrite/--no-overwrite"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview the file names, write nothing."),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Explicit path to mysuite.toml."),
    quiet: bool = typer.Option(False, "--quiet", "-q"),
) -> None:
    try:
        input_files = _resolve(inputs, recursive)
        config = load_config(config_path)
        active_profile = profiles_mod.resolve(config)
    except (InvalidInputError, MysuiteConfigError, ProfileError) as exc:
        log_error(escape(str(exc)))
        raise typer.Exit(1) from exc
    meta = active_profile.metadata if active_profile else {}
    author = author or meta.get("author")
    if not author:
        log_error("protect needs an author: --author, or metadata.author in the active profile")
        raise typer.Exit(1)
    secret = _mark_key(key) if mark else None
    require_tools(config.tools, "metadata-strip")
    opts = ChainOptions(author=author, copyright_notice=copyright_notice or meta.get("copyright"), credit_line=credit_line,
                        source=source, usage_terms=usage_terms, declare_policy=policy, terms_url=terms_url or meta.get("terms_url"),
                        no_ai=True, mark_key=secret, mark_id=ident, c2pa=c2pa)
    steps = expand_steps(["protect"], mark=mark)
    jsonout.set_extra(steps=steps, profile=active_profile.name if active_profile else None)
    _chain_batch(input_files, ["protect"], steps, opts=opts, config=config, overwrite=overwrite, dry_run=dry_run, quiet=quiet)


@app.command(
    "apply",
    help="Apply a metadata policy in one go into ONE copy - the active profile's, or --policy: steps from strip, strip-private "
    "(keeps the credit), credit, declare, mark, or protect (= strip-private, credit, declare), in order. strip,credit writes "
    "name_stripped_credited.ext and nothing else.",
)
@jsonout.with_json("metadata-apply")
def apply(
    inputs: List[Path] = typer.Argument(..., exists=True, readable=True, help="Image files and/or folders."),
    policy: Optional[str] = typer.Option(None, "--policy", help="Comma-separated steps, e.g. strip,credit or protect (default: the profile's metadata.policy)."),
    author: Optional[str] = typer.Option(None, "--author", "-a", help="For the credit step (default: the profile's metadata.author)."),
    copyright_notice: Optional[str] = typer.Option(None, "--copyright", help="For the credit step (default: the profile's metadata.copyright)."),
    key: Optional[str] = typer.Option(None, "--key", help="Secret key for a mark step (better: MYSUITE_MARK_KEY)."),
    advanced: bool = typer.Option(False, "--advanced", hidden=True, help="Allow the randomize step."),
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
    requested = [s.strip() for s in policy.split(",") if s.strip()] if policy else (list(active_profile.policy) if active_profile else [])
    if not requested or any(s not in POLICIES for s in requested):
        log_error("give --policy with steps from: " + ", ".join(p for p in POLICIES if p != "randomize")
                  + " (or select a profile that has metadata.policy)")
        raise typer.Exit(1)
    if "randomize" in requested and not advanced:
        log_error(RANDOMIZE_REFUSED)
        raise typer.Exit(jsonout.EXIT_USAGE)
    steps = expand_steps(requested)
    meta = active_profile.metadata if active_profile else {}
    author = author or meta.get("author")
    if "credit" in steps and not author:
        log_error("the credit step needs an author: --author, or metadata.author in the profile")
        raise typer.Exit(1)
    secret = _mark_key(key) if "mark" in steps else None
    require_tools(config.tools, "metadata-strip")
    jsonout.set_extra(policy=requested, profile=active_profile.name if active_profile else None)
    opts = ChainOptions(author=author, copyright_notice=copyright_notice or meta.get("copyright"), terms_url=meta.get("terms_url"),
                        no_ai=bool(meta.get("no_ai")), mark_key=secret)
    _chain_batch(input_files, requested, steps, opts=opts, config=config, overwrite=overwrite, dry_run=dry_run, quiet=quiet)


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
