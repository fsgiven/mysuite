from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import typer
from rich.markup import escape

from mysuite.config import MysuiteConfigError, load_config
from mysuite.convert._parsing import InvalidInputError, RASTER_FORMATS, detect_source_format, resolve_input_files
from mysuite.specs import core
from mysuite.utils import jsonout
from mysuite.utils.console import console, log_error, log_skip, log_step
from mysuite.utils.subprocess_utils import MysuiteToolError


def _tools(config_path):
    try:
        return load_config(config_path).tools
    except MysuiteConfigError as exc:
        log_error(escape(str(exc)))
        raise typer.Exit(1) from exc


def _files(inputs, recursive, raster_only=False):
    try:
        files = resolve_input_files(inputs, recursive=recursive)
    except InvalidInputError as exc:
        log_error(escape(str(exc)))
        raise typer.Exit(1) from exc
    if raster_only:
        files = [f for f in files if detect_source_format(f) in RASTER_FORMATS]
        if not files:
            log_error("no raster images found")
            raise typer.Exit(1)
    return files


@jsonout.with_json("print")
def print_command(
    inputs: List[Path] = typer.Argument(..., exists=True, readable=True, help="Images (or SVG/PDF/EPS) to output at an exact size."),
    size: str = typer.Option(..., "--size", help="Exact output size: 10x15cm, 4x6in, 210x297mm or 1200x630px (plain numbers are pixels)."),
    dpi: float = typer.Option(300.0, "--dpi", help="Resolution for physical sizes, and the dpi written into the file."),
    fit: str = typer.Option("contain", "--fit", help="contain = whole picture inside (padded); cover = fill the size and crop; stretch = distort to fit."),
    background: Optional[str] = typer.Option(None, "--background", help="Padding colour for contain (default transparent; white for JPEG)."),
    gravity: str = typer.Option("center", "--gravity", help="Where the picture sits (contain) or which part is kept (cover)."),
    bleed: float = typer.Option(0.0, "--bleed", help="Print bleed in mm added around the size (0-50)."),
    auto_rotate: bool = typer.Option(False, "--auto-rotate", help="Turn the picture 90° when its orientation doesn't match the paper."),
    output_format: Optional[str] = typer.Option(None, "--format", help="png, jpeg, tiff or webp (default: same as the source)."),
    quality: int = typer.Option(95, "--quality", help="JPEG quality 1-100."),
    recursive: bool = typer.Option(False, "--recursive", "-r"),
    overwrite: bool = typer.Option(False, "--overwrite/--no-overwrite"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the pixel size that would be written."),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
) -> None:
    """Output at EXACTLY the size you ask for - 10x15cm at 300 dpi is 1181x1772 px - with the dpi stored in the file."""
    tools = _tools(config_path)
    files = _files(inputs, recursive)
    failed = 0
    for f in files:
        try:
            if dry_run:
                w, h, _ = core.parse_size(size, dpi)
                b = round(bleed / 25.4 * dpi)
                jsonout.add_item(input=f, status="planned", size=[w + 2 * b, h + 2 * b], dpi=dpi)
                console.print(f"[dim]{escape(str(f))}  ->  {w + 2 * b}x{h + 2 * b} px @ {dpi:g} dpi[/dim]")
                continue
            r = core.print_size(f, size, dpi=dpi, fit=fit, background=background, fmt=output_format, quality=quality,
                                gravity=gravity, bleed_mm=bleed, auto_rotate=auto_rotate, tools=tools, overwrite=overwrite)
        except (core.SpecError, MysuiteToolError) as exc:
            failed += 1
            jsonout.add_item(input=f, status="failed", error=str(exc))
            log_error(f"{escape(str(f))}: {escape(str(exc))}")
            continue
        jsonout.add_item(input=f, output=r.path, status=r.status, **({"size": list(r.size), "dpi": r.dpi, "notes": r.notes} if r.status == "written" else {}))
        for n in r.notes:
            jsonout.add_warning(f"{f.name}: {n}")
            log_skip(escape(f"{f.name}: {n}"))
        if r.status == "written":
            log_step(f"{escape(str(r.path))}  [dim]{r.size[0]}x{r.size[1]} px @ {r.dpi:g} dpi[/dim]")
        else:
            console.print(f"[dim]— exists, skipped: {escape(str(r.path))}[/dim]")
    if failed:
        raise typer.Exit(1)


@jsonout.with_json("rename")
def rename_command(
    inputs: List[Path] = typer.Argument(..., exists=True, readable=True, help="Files and/or folders to rename."),
    pattern: str = typer.Option(..., "--pattern", help="New name with tokens: {name} {ext} {n} {n:3} {date} {datetime} {w} {h}, e.g. 'trip_{n:3}{ext}'."),
    start: int = typer.Option(1, "--start", help="Number of the first file ({n})."),
    sort: str = typer.Option("name", "--sort", help="Order for {n}: name, date (EXIF capture time, else file time) or size."),
    out: Optional[Path] = typer.Option(None, "--out", "-o", help="Folder for the renamed COPIES (default: beside the originals)."),
    move: bool = typer.Option(False, "--move", help="Rename the originals in place instead of making renamed copies."),
    recursive: bool = typer.Option(False, "--recursive", "-r"),
    overwrite: bool = typer.Option(False, "--overwrite/--no-overwrite"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the old -> new names, change nothing."),
) -> None:
    """Rename many files by a pattern. Safe by default: copies are made and originals stay; --move renames in place."""
    from mysuite import sandbox

    try:
        files = resolve_input_files(inputs, recursive=recursive)
        plans = core.plan_rename(files, pattern, start=start, sort=sort, out_dir=out)
        sandbox.check_inputs([p.source for p in plans])
        for p in plans:
            sandbox.check_write(p.target)
    except (InvalidInputError, core.SpecError) as exc:
        log_error(escape(str(exc)))
        raise typer.Exit(1) from exc
    if not dry_run:
        core.run_rename(plans, move=move, overwrite=overwrite)
    for p in plans:
        jsonout.add_item(input=p.source, output=p.target, status=p.status if not dry_run else "planned")
        arrow = f"{escape(p.source.name)} -> {escape(p.target.name)}"
        if dry_run or p.status == "planned":
            console.print(f"[dim]{arrow}[/dim]")
        elif p.status == "skipped_existing":
            console.print(f"[dim]— exists, skipped: {arrow}[/dim]")
        else:
            log_step(arrow)


@jsonout.with_json("sheet")
def sheet_command(
    inputs: List[Path] = typer.Argument(..., exists=True, readable=True, help="Images / folders to put on one sheet."),
    out: Path = typer.Option(Path("contact-sheet.png"), "--out", "-o", help="Output file: .png, .jpg or .pdf."),
    columns: int = typer.Option(4, "--columns", help="Pictures per row (1-20)."),
    cell: int = typer.Option(240, "--cell", help="Size of each picture's box in pixels (32-1200)."),
    no_labels: bool = typer.Option(False, "--no-labels", help="Leave out the file names."),
    background: str = typer.Option("#1b1d22", "--background", help="Sheet colour."),
    title: Optional[str] = typer.Option(None, "--title", help="A heading."),
    recursive: bool = typer.Option(False, "--recursive", "-r"),
    overwrite: bool = typer.Option(False, "--overwrite/--no-overwrite"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
) -> None:
    """One picture of many: a contact sheet with labels, to scan a folder in one look."""
    tools = _tools(config_path)
    files = _files(inputs, recursive)
    try:
        r = core.contact_sheet(files, out, tools, columns=columns, cell=cell, labels=not no_labels, background=background,
                               title=title, overwrite=overwrite)
    except (core.SpecError, MysuiteToolError) as exc:
        jsonout.add_item(status="failed", error=str(exc))
        log_error(escape(str(exc)))
        raise typer.Exit(1) from exc
    jsonout.add_item(output=r["path"], status=r["status"], **{k: (list(v) if isinstance(v, tuple) else v) for k, v in r.items() if k not in ("path", "status")})
    if r["status"] == "written":
        log_step(f"{escape(str(r['path']))}  [dim]{r['images']} image(s)[/dim]")
        for name in r["failed"]:
            log_skip(escape(f"couldn't draw {name}"))
            jsonout.add_warning(f"couldn't draw {name}")
    else:
        console.print(f"[dim]— exists, skipped: {escape(str(r['path']))}[/dim]")


@jsonout.with_json("profile")
def profile_command(
    inputs: List[Path] = typer.Argument(..., exists=True, readable=True, help="Raster images."),
    to: str = typer.Option(..., "--to", help="srgb (for screens/web) or cmyk (for print; same engine as export)."),
    cmyk_mode: str = typer.Option("exact", "--cmyk-mode", help="exact, clean or clean:N (see export)."),
    cmyk_profile: Optional[Path] = typer.Option(None, "--cmyk-profile", exists=True, dir_okay=False, help="CMYK ICC profile (default: Ghostscript's SWOP)."),
    output_format: Optional[str] = typer.Option(None, "--format", help="png/jpeg/tiff/webp (cmyk: tiff or jpeg)."),
    quality: int = typer.Option(95, "--quality"),
    recursive: bool = typer.Option(False, "--recursive", "-r"),
    overwrite: bool = typer.Option(False, "--overwrite/--no-overwrite"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
) -> None:
    """Convert colour profiles: Adobe RGB / Display P3 / CMYK photos to a correct sRGB copy, or RGB to CMYK for print."""
    tools = _tools(config_path)
    failed = 0
    for f in _files(inputs, recursive, raster_only=True):
        try:
            r = core.convert_profile(f, to, tools=tools, cmyk_mode=cmyk_mode, cmyk_profile=str(cmyk_profile) if cmyk_profile else None,
                                     fmt=output_format, quality=quality, overwrite=overwrite)
        except (core.SpecError, MysuiteToolError, Exception) as exc:  # noqa: BLE001 - CmykError and friends are ValueErrors
            failed += 1
            jsonout.add_item(input=f, status="failed", error=str(exc))
            log_error(f"{escape(str(f))}: {escape(str(exc))}")
            continue
        jsonout.add_item(input=f, output=r.path, status=r.status, **({"notes": r.notes} if r.notes else {}))
        for n in r.notes:
            jsonout.add_warning(f"{f.name}: {n}")
            log_skip(escape(f"{f.name}: {n}"))
        if r.status == "written":
            log_step(escape(str(r.path)))
        else:
            console.print(f"[dim]— exists, skipped: {escape(str(r.path))}[/dim]")
    if failed:
        raise typer.Exit(1)
