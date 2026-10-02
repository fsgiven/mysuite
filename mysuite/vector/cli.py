from __future__ import annotations

import gzip
import shutil
from pathlib import Path
from typing import List, Optional

import typer
from rich.markup import escape

from mysuite import sandbox
from mysuite.config import MysuiteConfigError, load_config
from mysuite.doctor import require_tools
from mysuite.utils import jsonout
from mysuite.utils.console import console, log_error, log_skip, log_step
from mysuite.utils.subprocess_utils import MysuiteToolError, atomic_write_via
from mysuite.vector import imports as imp
from mysuite.vector.svgopt import OptimiseError, optimise
from mysuite.vector.verify import renders_same

app = typer.Typer(help="Vector files: bring PDF / AI / EPS in as SVG, and make SVGs smaller without changing how they look.", no_args_is_help=True)

_SVG_LIKE = {".svg", ".svgz"}
_IMPORTABLE = imp.IMPORT_EXTENSIONS


def _expand(inputs: List[Path], extensions: set[str] | frozenset[str], recursive: bool) -> list[Path]:
    found: list[Path] = []
    seen: set[Path] = set()
    for item in inputs:
        if item.is_dir():
            pattern = "**/*" if recursive else "*"
            files = sorted(p for p in item.glob(pattern) if p.is_file() and p.suffix.lower() in extensions and not p.name.startswith("."))
        else:
            files = [item]
        for f in files:
            key = f.resolve()
            if key not in seen:
                seen.add(key)
                found.append(f)
    if not found:
        raise ValueError("no matching files found (" + ", ".join(sorted(extensions)) + ")")
    sandbox.check_inputs(found)
    return found


@app.command("import", help="Bring PDF, Illustrator (.ai), EPS or SVGZ files in as plain SVG (text becomes outlines). Then recolor, tokens and variants work on them.")
@jsonout.with_json("svg-import")
def import_command(
    inputs: List[Path] = typer.Argument(..., exists=True, readable=True, help="PDF / .ai / .eps / .svgz files and/or folders."),
    page: int = typer.Option(1, "--page", min=1, help="Which page to import."),
    all_pages: bool = typer.Option(False, "--all-pages", help="Import every page, as <name>-1.svg, <name>-2.svg, …"),
    crop: bool = typer.Option(True, "--crop/--no-crop", help="Trim to the drawn content instead of keeping the whole page (a logo on an A4 sheet)."),
    optimise_result: bool = typer.Option(True, "--optimise/--no-optimise", help="Tidy the result: colours as plain hex, numbers rounded, no leftovers."),
    out: Optional[Path] = typer.Option(None, "--out", "-o", help="Output folder (default: beside each source)."),
    recursive: bool = typer.Option(False, "--recursive", "-r", help="Recurse into subdirectories."),
    overwrite: bool = typer.Option(False, "--overwrite/--no-overwrite", help="Overwrite existing files."),
    dry_run: bool = typer.Option(False, "--dry-run", help="List what would be written."),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Explicit path to mysuite.toml."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Print only the summary."),
) -> None:
    try:
        files = _expand(inputs, _IMPORTABLE, recursive)
        config = load_config(config_path)
    except (ValueError, MysuiteConfigError) as exc:
        log_error(escape(str(exc)))
        raise typer.Exit(1) from exc
    require_tools(config.tools, "svg-import")

    written = skipped = failed = 0
    import tempfile

    for src in files:
        folder = out or src.parent
        try:
            pages = imp.page_count(src) if src.suffix.lower() != ".svgz" and imp._kind(src) == "pdf" else 1
        except (imp.ImportError_, OSError):
            pages = 1
        wanted = list(range(1, pages + 1)) if all_pages and pages > 1 else [page]
        for number in wanted:
            stem = f"{src.stem}-{number}" if all_pages and pages > 1 else src.stem
            target = folder / f"{stem}.svg"
            if dry_run:
                jsonout.add_item(input=src, output=target, status="planned", page=number)
                if not quiet:
                    console.print(f"[dim]{escape(str(src))} -> {escape(str(target))}[/dim]")
                continue
            if target.exists() and not overwrite:
                skipped += 1
                jsonout.add_item(input=src, output=target, status="skipped_existing", page=number)
                if not quiet:
                    console.print(f"[dim]— exists, skipped: {escape(str(target))}[/dim]")
                continue
            try:
                sandbox.check_write(target)
                with tempfile.TemporaryDirectory(prefix="mysuite-svgimport-") as tmp:
                    result = imp.to_svg(src, Path(tmp), page=number, tools=config.tools, crop=crop, name=stem)
                    text = result.svg.read_text(encoding="utf-8")
                    notes = list(result.notes)
                    if optimise_result:
                        tidy = optimise(text, precision=3)
                        text = tidy.text
                        notes.append(f"tidied: {tidy.saved_percent}% smaller")
                    atomic_write_via(target, lambda tmp_path: tmp_path.write_text(text, encoding="utf-8"))
            except (imp.ImportError_, OptimiseError, MysuiteToolError, OSError) as exc:
                failed += 1
                jsonout.add_item(input=src, status="failed", error=str(exc))
                log_error(f"{escape(str(src))}: {escape(str(exc))}")
                continue
            written += 1
            jsonout.add_item(input=src, output=target, status="written", page=number, pages=result.pages, notes=notes, bytes=len(text.encode()))
            for note in notes:
                if "raster" in note or "pages" in note:
                    jsonout.add_warning(f"{src.name}: {note}")
            if not quiet:
                log_step(escape(str(target)))
                for note in notes:
                    (log_skip if "raster" in note else lambda m: console.print(f"[dim]  {m}[/dim]"))(escape(f"{src.name}: {note}"))
    if dry_run:
        return
    console.print(f"\n[bold green]done[/bold green] — {written} written, {skipped} already existed (use --overwrite to replace), {failed} failed")
    if failed:
        raise typer.Exit(1)


@app.command("optimise", help="Make SVGs smaller without changing how they look: no editor leftovers, tidy colours, rounded numbers. Checked by rendering both versions.")
@jsonout.with_json("svg-optimise")
def optimise_command(
    inputs: List[Path] = typer.Argument(..., exists=True, readable=True, help="SVG / .svgz files and/or folders."),
    precision: int = typer.Option(3, "--precision", min=0, max=8, help="Decimals kept in coordinates (3 is invisible at normal sizes; small viewBoxes automatically keep 2 more)."),
    keep_ids: bool = typer.Option(False, "--keep-ids", help="Keep every id (needed if scripts or other files point into the SVG)."),
    remove_title: bool = typer.Option(False, "--remove-title", help="Also drop <title> and <desc> (they help screen readers)."),
    verify: bool = typer.Option(True, "--verify/--no-verify", help="Render before and after and refuse to write a file that looks different."),
    svgz: bool = typer.Option(False, "--svgz", help="Write gzip-compressed .svgz instead of .svg."),
    out: Optional[Path] = typer.Option(None, "--out", "-o", help="Output folder (default: beside each source, as <name>.min.svg)."),
    recursive: bool = typer.Option(False, "--recursive", "-r", help="Recurse into subdirectories."),
    overwrite: bool = typer.Option(False, "--overwrite/--no-overwrite", help="Overwrite existing files."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Report the savings, write nothing."),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Explicit path to mysuite.toml."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Print only the summary."),
) -> None:
    try:
        files = _expand(inputs, _SVG_LIKE, recursive)
        config = load_config(config_path)
    except (ValueError, MysuiteConfigError) as exc:
        log_error(escape(str(exc)))
        raise typer.Exit(1) from exc
    if verify:
        require_tools(config.tools, "svg-optimise")

    total_before = total_after = written = skipped = failed = 0
    for src in files:
        folder = out or src.parent
        target = folder / (src.stem.removesuffix(".min") + ".min" + (".svgz" if svgz else ".svg"))
        if src.suffix.lower() == ".svgz":
            target = folder / (src.name[:-5].removesuffix(".min") + ".min" + (".svgz" if svgz else ".svg"))
        if target.exists() and not overwrite and not dry_run:
            skipped += 1
            jsonout.add_item(input=src, output=target, status="skipped_existing")
            if not quiet:
                console.print(f"[dim]— exists, skipped: {escape(str(target))}[/dim]")
            continue
        try:
            raw = src.read_bytes()
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
                if len(raw) > imp.MAX_UNZIPPED_BYTES:
                    raise OptimiseError("unpacks to more than 100 MB; refusing")
            text = raw.decode("utf-8")
            result = optimise(text, precision=precision, keep_ids=keep_ids, keep_title=not remove_title)
            notes = list(result.notes)
            if verify:
                same, mean, edge = renders_same(text, result.text, config.tools)
                if not same:
                    raise OptimiseError(f"the optimised file would look different (mean difference {mean}, edge {edge}); not written — try --precision {min(8, precision + 2)} or --keep-ids")
                notes.append(f"verified: renders the same (mean difference {mean}/255)")
            if not dry_run:
                sandbox.check_write(target)
                payload = result.text.encode("utf-8")
                if svgz:
                    payload = gzip.compress(payload, mtime=0)
                atomic_write_via(target, lambda tmp_path: tmp_path.write_bytes(payload))
        except (OptimiseError, UnicodeDecodeError, MysuiteToolError, OSError) as exc:
            failed += 1
            jsonout.add_item(input=src, status="failed", error=str(exc))
            log_error(f"{escape(str(src))}: {escape(str(exc))}")
            continue
        written += 0 if dry_run else 1
        total_before += result.before
        total_after += result.after
        jsonout.add_item(input=src, output=target, status="planned" if dry_run else "written",
                         bytes_before=result.before, bytes_after=result.after, saved_percent=result.saved_percent, notes=notes)
        if not quiet:
            line = f"{escape(str(target))}  [dim]{result.before} → {result.after} bytes, {result.saved_percent}% smaller[/dim]"
            (console.print(f"[dim]would write[/dim] {line}") if dry_run else log_step(line))
            for note in result.notes:
                log_skip(escape(note))
    saved = round(100 * (1 - total_after / total_before), 1) if total_before else 0.0
    jsonout.set_extra(total_bytes_before=total_before, total_bytes_after=total_after, saved_percent=saved)
    console.print(f"\n[bold green]{'dry run' if dry_run else 'done'}[/bold green] — {written} written, {skipped} already existed, {failed} failed; {total_before} → {total_after} bytes ({saved}% smaller)")
    if failed:
        raise typer.Exit(1)
