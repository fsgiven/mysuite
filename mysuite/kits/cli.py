from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import typer
from rich.markup import escape
from rich.table import Table

from mysuite.config import MysuiteConfigError, load_config
from mysuite.convert._parsing import InvalidInputError, resolve_input_files
from mysuite.kits.kits import KITS, KitError, make_kit, retina_kit
from mysuite.utils import jsonout
from mysuite.utils.console import console, log_error, log_skip, log_step
from mysuite.utils.subprocess_utils import MysuiteToolError

app = typer.Typer(help="Make a whole set of files with exact, well-known specs from one logo (favicons, app icons, social images, retina).", no_args_is_help=True)


@app.command("list", help="List the available kits and what each one makes.")
@jsonout.with_json("kit-list")
def list_kits() -> None:
    table = Table(title="kits")
    table.add_column("kit")
    table.add_column("files")
    table.add_column("what")
    for kit in [*KITS.values(), retina_kit(64)]:
        count = len(kit.files)
        jsonout.add_item(kit=kit.name, files=count, description=kit.description,
                         outputs=[f.path for f in kit.files])
        table.add_row(kit.name, str(count), escape(kit.description))
    console.print(table)


@app.command("make", help="Make a kit from a logo: `mysuite kit make logo.svg --kit favicon`.")
@jsonout.with_json("kit")
def make(
    inputs: List[Path] = typer.Argument(..., exists=True, readable=True, help="Logo file(s): SVG, PNG, JPEG, WebP, PDF, EPS…"),
    kit: str = typer.Option(..., "--kit", "-k", help=f"Which kit: {', '.join([*KITS, 'retina'])}. See `mysuite kit list`."),
    out: Path = typer.Option(Path("kits"), "--out", "-o", help="Output folder. Files go to <out>/<logo name>/<kit>/…"),
    name: Optional[str] = typer.Option(None, "--name", help="Name used in the folder and file names (default: the logo's file name)."),
    background: Optional[str] = typer.Option(None, "--background", help="Colour behind the logo (e.g. '#ffffff'). Needed for social images; opaque icons default to white."),
    padding: Optional[float] = typer.Option(None, "--padding", help="Empty border around the logo, percent of the canvas (0-49)."),
    size: int = typer.Option(64, "--size", help="Retina kit only: the @1x size in pixels (@2x and @3x follow)."),
    recursive: bool = typer.Option(False, "--recursive", "-r", help="Recurse into subdirectories."),
    overwrite: bool = typer.Option(False, "--overwrite/--no-overwrite", help="Overwrite existing files."),
    dry_run: bool = typer.Option(False, "--dry-run", help="List the files that would be written."),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Explicit path to mysuite.toml."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Print only the summary."),
) -> None:
    try:
        chosen = retina_kit(size) if kit == "retina" else KITS.get(kit)
        if chosen is None:
            raise KitError(f"unknown kit {kit!r} - expected one of {', '.join([*KITS, 'retina'])}")
        logos = resolve_input_files(inputs, recursive=recursive)
        config = load_config(config_path)
    except (KitError, InvalidInputError, MysuiteConfigError) as exc:
        log_error(escape(str(exc)))
        raise typer.Exit(1) from exc

    written = skipped = failed = 0
    for logo in logos:
        try:
            result = make_kit(chosen, logo, out, tools=config.tools, name=name if len(logos) == 1 else None,
                              background=background, padding=padding, overwrite=overwrite, dry_run=dry_run)
        except (KitError, MysuiteToolError) as exc:
            failed += 1
            jsonout.add_item(input=logo, kit=chosen.name, status="failed", error=str(exc))
            log_error(f"{escape(str(logo))}: {escape(str(exc))}")
            continue
        for f in result.files:
            item = dict(input=logo, kit=chosen.name, output=f.path, kind=f.kind, status=f.status)
            if f.size:
                item["size"] = list(f.size)
            if f.note:
                item["note"] = f.note
                jsonout.add_warning(f"{f.path.name}: {f.note}")
            jsonout.add_item(**item)
            if f.status == "written":
                written += 1
                if not quiet:
                    log_step(escape(str(f.path)))
            elif f.status == "skipped_existing":
                skipped += 1
                if not quiet:
                    console.print(f"[dim]— exists, skipped: {escape(str(f.path))}[/dim]")
            elif f.status == "planned" and not quiet:
                console.print(f"[dim]{escape(str(f.path))}[/dim]")
            if f.note and not quiet:
                log_skip(escape(f.note))
    if dry_run:
        return
    if not quiet:
        console.print(f"\n[bold green]done[/bold green] — {written} written, {skipped} already existed "
                      f"(use --overwrite to replace), {failed} failed")
    if failed:
        raise typer.Exit(1)
