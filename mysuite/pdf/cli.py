from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import typer
from rich.markup import escape
from rich.table import Table

from mysuite.config import MysuiteConfigError, load_config
from mysuite.pdf import ops
from mysuite.utils import jsonout
from mysuite.utils.console import console, log_error, log_skip, log_step
from mysuite.utils.subprocess_utils import MysuiteToolError

app = typer.Typer(help="PDF toolbox: merge, split, extract, rotate, resize, number, stamp, strip, compress, images in/out.", no_args_is_help=True)

PDFS = typer.Argument(..., exists=True, readable=True, dir_okay=False, help="PDF file(s).")
OUT = typer.Option(None, "--out", "-o", help="Output file (or folder). Default: beside the source with a suffix.")
OVERWRITE = typer.Option(False, "--overwrite/--no-overwrite", help="Overwrite existing outputs.")
CONFIG = typer.Option(None, "--config", "-c", help="Explicit path to mysuite.toml.")


def _check_inputs(paths: list[Path]) -> list[Path]:
    from mysuite import sandbox

    sandbox.check_inputs([Path(p).resolve() for p in paths])
    return [Path(p) for p in paths]


def _report(source, results: list[ops.OutFile], quiet: bool = False) -> int:
    failed = 0
    for r in results:
        item: dict = dict(input=source, output=r.path, status=r.status)
        if r.pages is not None:
            item["pages"] = r.pages
        if r.note:
            item["note"] = r.note
        item.update(r.extra)
        jsonout.add_item(**item)
        if r.note and r.status != "failed":
            jsonout.add_warning(f"{r.path.name}: {r.note}")
            log_skip(escape(f"{r.path.name}: {r.note}"))
        if r.status == "failed":
            failed += 1
            log_error(escape(r.note or f"{r.path.name} failed"))
        elif r.status == "skipped_existing":
            if not quiet:
                console.print(f"[dim]— exists, skipped: {escape(str(r.path))}[/dim]")
        elif not quiet:
            log_step(f"{escape(str(r.path))}" + (f"  [dim]{r.pages} page(s)[/dim]" if r.pages is not None else ""))
    return failed


def _run_each(paths: list[Path], fn, quiet: bool) -> None:
    failed = 0
    for p in paths:
        try:
            result = fn(p)
        except (ops.PdfError, MysuiteToolError) as exc:
            failed += 1
            jsonout.add_item(input=p, status="failed", error=str(exc))
            log_error(f"{escape(str(p))}: {escape(str(exc))}")
            continue
        failed += _report(p, result if isinstance(result, list) else [result], quiet)
    if failed:
        raise typer.Exit(1)


def _tools():
    try:
        return load_config(None).tools
    except MysuiteConfigError as exc:
        log_error(escape(str(exc)))
        raise typer.Exit(1) from exc


@app.command("info", help="Pages, page sizes, rotation, metadata, forms - read only.")
@jsonout.with_json("pdf-info")
def info(files: List[Path] = PDFS) -> None:
    failed = 0
    for p in _check_inputs(files):
        try:
            data = ops.info(p)
        except ops.PdfError as exc:
            failed += 1
            jsonout.add_item(input=p, status="failed", error=str(exc))
            log_error(f"{escape(str(p))}: {escape(str(exc))}")
            continue
        jsonout.add_item(status="ok", **data)
        table = Table(title=escape(data["path"]), show_header=False)
        for k, v in data.items():
            if k != "path":
                table.add_row(escape(k), escape(str(v)))
        console.print(table)
    if failed:
        raise typer.Exit(1)


@app.command("merge", help="Join PDFs into one, in the order given.")
@jsonout.with_json("pdf-merge")
def merge(files: List[Path] = PDFS, out: Optional[Path] = OUT, overwrite: bool = OVERWRITE, quiet: bool = typer.Option(False, "--quiet", "-q")) -> None:
    try:
        result = ops.merge(_check_inputs(files), out, overwrite)
    except ops.PdfError as exc:
        jsonout.add_item(status="failed", error=str(exc))
        log_error(escape(str(exc)))
        raise typer.Exit(1) from exc
    if _report(files[0], [result], quiet):
        raise typer.Exit(1)


@app.command("split", help="Cut a PDF into pieces: --every N pages, or --ranges '1-3,4-6'.")
@jsonout.with_json("pdf-split")
def split(files: List[Path] = PDFS, every: Optional[int] = typer.Option(None, "--every", help="Pages per piece."),
          ranges: Optional[str] = typer.Option(None, "--ranges", help="Comma-separated page ranges, one file each."),
          out: Optional[Path] = typer.Option(None, "--out", "-o", help="Output folder (default: beside the source)."),
          overwrite: bool = OVERWRITE, quiet: bool = typer.Option(False, "--quiet", "-q")) -> None:
    _run_each(_check_inputs(files), lambda p: ops.split(p, every, ranges, out, overwrite), quiet)


@app.command("extract", help="Keep only some pages: --pages '1,3-5,8-' (also last, odd, even).")
@jsonout.with_json("pdf-extract")
def extract(files: List[Path] = PDFS, pages: str = typer.Option(..., "--pages", help="Pages to keep, e.g. '1,3-5,8-', last, odd, even."),
            out: Optional[Path] = OUT, overwrite: bool = OVERWRITE, quiet: bool = typer.Option(False, "--quiet", "-q")) -> None:
    _run_each(_check_inputs(files), lambda p: ops.extract(p, pages, out, overwrite), quiet)


@app.command("rotate", help="Rotate pages clockwise by 90/180/270 degrees.")
@jsonout.with_json("pdf-rotate")
def rotate(files: List[Path] = PDFS, degrees: int = typer.Option(90, "--degrees", help="Clockwise: 90, 180 or 270 (or -90)."),
           pages: str = typer.Option("all", "--pages", help="Which pages (default all)."),
           out: Optional[Path] = OUT, overwrite: bool = OVERWRITE, quiet: bool = typer.Option(False, "--quiet", "-q")) -> None:
    _run_each(_check_inputs(files), lambda p: ops.rotate(p, degrees, pages, out, overwrite), quiet)


@app.command("resize", help="Fit every page onto a standard size (a4, letter, 210x297mm …), centred, content scaled to fit.")
@jsonout.with_json("pdf-resize")
def resize(files: List[Path] = PDFS, size: str = typer.Option(..., "--size", help="a0-a6, b5, letter, legal, tabloid, or 210x297mm / 8.5x11in / 595x842 (points)."),
           out: Optional[Path] = OUT, overwrite: bool = OVERWRITE, quiet: bool = typer.Option(False, "--quiet", "-q")) -> None:
    _run_each(_check_inputs(files), lambda p: ops.resize(p, size, out, overwrite), quiet)


@app.command("strip", help="Remove title, author, producer, dates and the XMP packet.")
@jsonout.with_json("pdf-strip")
def strip(files: List[Path] = PDFS, out: Optional[Path] = OUT, overwrite: bool = OVERWRITE, quiet: bool = typer.Option(False, "--quiet", "-q")) -> None:
    _run_each(_check_inputs(files), lambda p: ops.strip_metadata(p, out, overwrite), quiet)


@app.command("number", help="Add page numbers. Format may use {n} and {total}, e.g. 'Page {n} of {total}'.")
@jsonout.with_json("pdf-number")
def number(files: List[Path] = PDFS, position: str = typer.Option("bottom-center", "--position", help=f"{', '.join(ops.POSITIONS)}"),
           format: str = typer.Option("{n}", "--format", help="Label, e.g. 'Page {n} of {total}'."),
           start: int = typer.Option(1, "--start", help="Number of the first page."),
           size: float = typer.Option(10.0, "--size", help="Font size in points (4-72)."),
           color: str = typer.Option("#333333", "--color", help="Text colour."),
           pages: str = typer.Option("all", "--pages", help="Which pages get a number."),
           out: Optional[Path] = OUT, overwrite: bool = OVERWRITE, quiet: bool = typer.Option(False, "--quiet", "-q")) -> None:
    tools = _tools()
    _run_each(_check_inputs(files), lambda p: ops.number_pages(p, out, overwrite, tools, position=position, fmt=format, start=start,
                                                               size=size, color=color, pages=pages), quiet)


@app.command("stamp", help="Stamp text and/or a logo on pages (e.g. DRAFT, CONFIDENTIAL), with opacity and angle.")
@jsonout.with_json("pdf-stamp")
def stamp(files: List[Path] = PDFS, text: Optional[str] = typer.Option(None, "--text", help="Text to stamp, e.g. DRAFT."),
          logo: Optional[Path] = typer.Option(None, "--logo", exists=True, dir_okay=False, help="PNG/JPEG logo to stamp (raster)."),
          position: str = typer.Option("center", "--position", help=f"{', '.join(ops.POSITIONS)}"),
          opacity: float = typer.Option(0.25, "--opacity", help="0-1."),
          angle: float = typer.Option(0.0, "--angle", help="Degrees counter-clockwise (e.g. 45 for a diagonal stamp)."),
          size: float = typer.Option(48.0, "--size", help="Text size in points."),
          color: str = typer.Option("#cc0000", "--color", help="Text colour."),
          scale: float = typer.Option(20.0, "--scale", help="Logo width in percent of the page width."),
          pages: str = typer.Option("all", "--pages", help="Which pages."),
          out: Optional[Path] = OUT, overwrite: bool = OVERWRITE, quiet: bool = typer.Option(False, "--quiet", "-q")) -> None:
    tools = _tools()
    if logo:
        _check_inputs([logo])
    _run_each(_check_inputs(files), lambda p: ops.stamp(p, out, overwrite, tools, text=text, logo=logo, position=position, opacity=opacity,
                                                         angle=angle, size=size, color=color, scale=scale, pages=pages), quiet)


@app.command("images", help="Extract the embedded pictures of a PDF as files.")
@jsonout.with_json("pdf-images")
def images(files: List[Path] = PDFS, out: Optional[Path] = typer.Option(None, "--out", "-o", help="Output folder."),
           overwrite: bool = OVERWRITE, quiet: bool = typer.Option(False, "--quiet", "-q")) -> None:
    _run_each(_check_inputs(files), lambda p: ops.extract_images(p, out, overwrite), quiet)


@app.command("render", help="Turn pages into PNG/JPEG images at a chosen resolution (Ghostscript).")
@jsonout.with_json("pdf-render")
def render(files: List[Path] = PDFS, dpi: int = typer.Option(150, "--dpi", help="Resolution, 10-1200."),
           format: str = typer.Option("png", "--format", help="png or jpeg."),
           pages: str = typer.Option("all", "--pages", help="Which pages."),
           out: Optional[Path] = typer.Option(None, "--out", "-o", help="Output folder."),
           overwrite: bool = OVERWRITE, quiet: bool = typer.Option(False, "--quiet", "-q")) -> None:
    tools = _tools()
    _run_each(_check_inputs(files), lambda p: ops.render(p, out, overwrite, tools, dpi=dpi, fmt=format, pages=pages), quiet)


@app.command("from-images", help="Make a PDF from images (one page each), optionally fitted on a paper size.")
@jsonout.with_json("pdf-from-images")
def from_images(files: List[Path] = typer.Argument(..., exists=True, readable=True, dir_okay=False, help="Image files, in page order."),
                size: Optional[str] = typer.Option(None, "--size", help="Paper size (a4, letter, 210x297mm …); default: each page is its image's size."),
                dpi: int = typer.Option(300, "--dpi", help="Resolution used for the pages."),
                margin: float = typer.Option(0.0, "--margin", help="Margin in mm when --size is used."),
                out: Optional[Path] = OUT, overwrite: bool = OVERWRITE, quiet: bool = typer.Option(False, "--quiet", "-q")) -> None:
    try:
        result = ops.from_images(_check_inputs(files), out, overwrite, size=size, dpi=dpi, margin_mm=margin)
    except ops.PdfError as exc:
        jsonout.add_item(status="failed", error=str(exc))
        log_error(escape(str(exc)))
        raise typer.Exit(1) from exc
    if _report(files[0], [result], quiet):
        raise typer.Exit(1)


@app.command("compress", help="Make a PDF smaller (Ghostscript): screen < ebook < printer < prepress.")
@jsonout.with_json("pdf-compress")
def compress(files: List[Path] = PDFS, level: str = typer.Option("ebook", "--level", help="screen (smallest), ebook, printer, prepress (best quality)."),
             out: Optional[Path] = OUT, overwrite: bool = OVERWRITE, quiet: bool = typer.Option(False, "--quiet", "-q")) -> None:
    tools = _tools()
    _run_each(_check_inputs(files), lambda p: ops.compress(p, level, out, overwrite, tools), quiet)
