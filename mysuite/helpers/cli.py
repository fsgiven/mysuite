from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import typer
from rich.markup import escape
from rich.table import Table

from mysuite import sandbox
from mysuite.config import MysuiteConfigError, load_config
from mysuite.convert._parsing import InvalidInputError, RASTER_FORMATS, detect_source_format, resolve_input_files
from mysuite.doctor import require_tools
from mysuite.helpers import core
from mysuite.utils import jsonout
from mysuite.utils.console import console, log_error, log_skip, log_step

CONFIG = typer.Option(None, "--config", "-c", help="Explicit path to mysuite.toml.")


def _config():
    try:
        return load_config(None)
    except MysuiteConfigError as exc:
        log_error(escape(str(exc)))
        raise typer.Exit(1) from exc


def _images(inputs: List[Path], recursive: bool) -> list[Path]:
    try:
        return [f for f in resolve_input_files(inputs, recursive=recursive) if detect_source_format(f) in RASTER_FORMATS]
    except InvalidInputError as exc:
        log_error(escape(str(exc)))
        raise typer.Exit(1) from exc


# ------------------------------------------------------------------------------------------ ocr
@jsonout.with_json("ocr")
def ocr(
    inputs: List[Path] = typer.Argument(..., exists=True, readable=True, help="Images / screenshots / scans."),
    languages: Optional[str] = typer.Option(None, "--languages", help="Recognition languages, e.g. en-US,de-DE (default: automatic)."),
    fast: bool = typer.Option(False, "--fast", help="Faster, less accurate."),
    save: bool = typer.Option(False, "--save", help="Also write <name>.txt beside each image (never overwrites without --overwrite)."),
    overwrite: bool = typer.Option(False, "--overwrite/--no-overwrite", help="Overwrite existing .txt files."),
    recursive: bool = typer.Option(False, "--recursive", "-r"),
) -> None:
    """Read the text in images (macOS Vision, on device)."""
    config = _config()
    require_tools(config.tools, "ocr")
    from mysuite.utils.subprocess_utils import atomic_write_via

    failed = 0
    for f in _images(inputs, recursive):
        try:
            data = core.ocr(f, config.tools, languages=languages, fast=fast)
        except core.HelperError as exc:
            failed += 1
            jsonout.add_item(input=f, status="failed", error=str(exc))
            log_error(f"{escape(str(f))}: {escape(str(exc))}")
            continue
        item = dict(input=f, status="ok", text=data["text"], line_count=len(data["lines"]), lines=data["lines"])
        if save:
            target = f.with_suffix(".txt")
            if target.exists() and not overwrite:
                item["saved"] = {"path": str(target), "status": "skipped_existing"}
            else:
                atomic_write_via(target, lambda tmp, t=data["text"]: tmp.write_text(t + "\n", encoding="utf-8"), preserve_extension=True)
                item["saved"] = {"path": str(target), "status": "written"}
        jsonout.add_item(**item)
        console.print(f"[bold]{escape(str(f))}[/bold]")
        console.print(escape(data["text"]) or "[dim](no text found)[/dim]")
    if failed:
        raise typer.Exit(1)


# ------------------------------------------------------------------------------------------- qr
qr_app = typer.Typer(help="Make a QR code, or read QR codes/barcodes from images.", no_args_is_help=True)


@qr_app.command("make", help="Make a QR code: `mysuite qr make \"https://example.com\" --out qr.png`.")
@jsonout.with_json("qr-make")
def qr_make(
    text: str = typer.Argument(..., help="What the code should say (a URL, text, Wi-Fi string…)."),
    out: Path = typer.Option(Path("qr.png"), "--out", "-o", help="Output file (.png or .svg)."),
    scale: int = typer.Option(10, "--scale", help="Pixels per module (PNG) / units (SVG), 1-100."),
    border: int = typer.Option(4, "--border", help="Quiet-zone modules around the code (the spec wants 4)."),
    error: str = typer.Option("m", "--error", help="Error correction: l (7%), m (15%), q (25%), h (30%)."),
    dark: str = typer.Option("#000000", "--dark", help="Colour of the modules."),
    light: str = typer.Option("#ffffff", "--light", help="Background colour."),
    overwrite: bool = typer.Option(False, "--overwrite/--no-overwrite"),
) -> None:
    try:
        result = core.make_qr(text, out, scale=scale, border=border, error=error, dark=dark, light=light, overwrite=overwrite)
    except core.HelperError as exc:
        jsonout.add_item(status="failed", error=str(exc))
        log_error(escape(str(exc)))
        raise typer.Exit(1) from exc
    jsonout.add_item(output=result["path"], status=result["status"], **{k: v for k, v in result.items() if k not in ("path", "status")})
    log_step(escape(str(result["path"]))) if result["status"] == "written" else console.print(f"[dim]— exists, skipped: {escape(str(result['path']))}[/dim]")


@qr_app.command("read", help="Read QR codes and barcodes in images (macOS Vision).")
@jsonout.with_json("qr-read")
def qr_read(inputs: List[Path] = typer.Argument(..., exists=True, readable=True), recursive: bool = typer.Option(False, "--recursive", "-r")) -> None:
    config = _config()
    require_tools(config.tools, "qr-read")
    failed = 0
    for f in _images(inputs, recursive):
        try:
            codes = core.read_codes(f, config.tools)
        except core.HelperError as exc:
            failed += 1
            jsonout.add_item(input=f, status="failed", error=str(exc))
            log_error(f"{escape(str(f))}: {escape(str(exc))}")
            continue
        jsonout.add_item(input=f, status="ok", count=len(codes), codes=codes)
        console.print(f"[bold]{escape(str(f))}[/bold]  {len(codes)} code(s)")
        for c in codes:
            console.print(f"  {escape(c['symbology'].replace('VNBarcodeSymbology', ''))}: {escape(c['payload'])}")
    if failed:
        raise typer.Exit(1)


# ---------------------------------------------------------------------------------------- dupes
@jsonout.with_json("dupes")
def dupes(
    inputs: List[Path] = typer.Argument(..., exists=True, readable=True, help="Folders and/or images to compare."),
    threshold: int = typer.Option(5, "--threshold", help="How different (bits of 64, 0-20) two pictures may be and still count as similar. 0 = look-alikes must hash identically."),
    recursive: bool = typer.Option(False, "--recursive", "-r", help="Recurse into subdirectories."),
) -> None:
    """Find identical and look-alike images (resized, re-saved, recompressed). Read only: nothing is deleted or moved."""
    files = _images(inputs, recursive)
    try:
        groups, errors = core.find_duplicates(files, threshold)
    except core.HelperError as exc:
        log_error(escape(str(exc)))
        raise typer.Exit(1) from exc
    for path, error in errors:
        jsonout.add_item(input=path, status="failed", error=error)
        log_error(escape(error))
    total_waste = 0
    for i, g in enumerate(groups, start=1):
        jsonout.add_item(status="group", group=i, **g)
        total_waste += g["wasted_bytes"]
        table = Table(title=f"group {i}: {g['kind']}")
        table.add_column("file")
        table.add_column("size")
        table.add_column("pixels")
        for f in g["files"]:
            keep = " (suggested keeper)" if f["path"] == g["keep_suggestion"] else ""
            table.add_row(escape(f["path"]) + keep, f"{f['bytes']:,} B", f"{f['width']}x{f['height']}")
        console.print(table)
    jsonout.set_extra(scanned=len(files), groups=len(groups), wasted_bytes=total_waste)
    console.print(f"\n[bold green]done[/bold green] — {len(files)} scanned, {len(groups)} group(s), {total_waste:,} bytes could be freed (nothing was touched)")
    if errors:
        raise typer.Exit(1)


# ------------------------------------------------------------------------------------------ diff
@jsonout.with_json("diff")
def diff(
    first: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True, help="Reference image."),
    second: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True, help="Image to compare."),
    threshold: int = typer.Option(16, "--threshold", help="A pixel counts as changed when any channel differs by more than this (0-255)."),
    out: Optional[Path] = typer.Option(None, "--out", "-o", help="Write a PNG with the changed pixels in red over a faded first image."),
    overwrite: bool = typer.Option(False, "--overwrite/--no-overwrite"),
) -> None:
    """Compare two images: size, how many pixels changed, PSNR - optionally with a picture of the changes."""
    sandbox.check_inputs([first.resolve(), second.resolve()])
    try:
        result = core.diff_images(first, second, threshold=threshold, out=out, overwrite=overwrite)
    except core.HelperError as exc:
        jsonout.add_item(status="failed", error=str(exc))
        log_error(escape(str(exc)))
        raise typer.Exit(1) from exc
    jsonout.add_item(status="ok", input=first, compared=second, **result)
    if result["note"]:
        jsonout.add_warning(result["note"])
        log_skip(escape(result["note"]))
    verdict = "identical" if result["identical"] else f"{result['changed_pct']}% of pixels changed"
    console.print(f"{verdict}  ·  mean difference {result['mean_abs_diff']}  ·  PSNR {result['psnr_db']} dB")


# -------------------------------------------------------------------------------------- contrast
@jsonout.with_json("contrast")
def contrast(
    foreground: Optional[str] = typer.Argument(None, help="Foreground colour, e.g. '#dd0000' (or use --logo)."),
    background: Optional[str] = typer.Argument(None, help="Background colour, e.g. white."),
    logo: Optional[Path] = typer.Option(None, "--logo", exists=True, dir_okay=False, help="Check every colour of this logo (SVG or image) against --on."),
    on: str = typer.Option("#ffffff", "--on", help="Background for --logo."),
) -> None:
    """WCAG contrast ratio and pass/fail for text (AA/AAA), large text and graphics."""
    pairs: list[tuple[str, str]] = []
    if logo is not None:
        sandbox.check_read(logo.resolve())
        kind = detect_source_format(logo)
        if kind == "svg":
            from mysuite.color.svg import palette

            colours = [h[:7] for h, _ in palette(logo.read_text(encoding="utf-8", errors="replace"))]
        elif kind in RASTER_FORMATS:
            from PIL import Image

            from mysuite.inspect.inspect import _palette_of

            with Image.open(logo) as im:
                im.seek(0)
                colours = [c["hex"] for c in _palette_of(im)]
        else:
            log_error("--logo must be an SVG or a raster image")
            raise typer.Exit(1)
        if not colours:
            log_error("no colours found in the logo")
            raise typer.Exit(1)
        pairs = [(c, on) for c in dict.fromkeys(colours)]
    elif foreground and background:
        pairs = [(foreground, background)]
    else:
        log_error("give a foreground and a background colour, or --logo FILE")
        raise typer.Exit(1)
    table = Table(title="contrast")
    for col in ("foreground", "background", "ratio", "AA text", "AA large", "AAA text", "graphics"):
        table.add_column(col)
    failed = False
    for fg, bg in pairs:
        try:
            r = core.check_colours(fg, bg)
        except core.HelperError as exc:
            failed = True
            jsonout.add_item(status="failed", error=str(exc))
            log_error(escape(str(exc)))
            continue
        jsonout.add_item(status="ok", **r)
        yes = lambda b: "[#4ADE80]pass[/]" if b else "[#F87171]fail[/]"
        table.add_row(r["foreground"], r["background"], f"{r['ratio']}:1", yes(r["AA_normal_text"]), yes(r["AA_large_text"]),
                      yes(r["AAA_normal_text"]), yes(r["graphics_and_ui_AA"]))
    console.print(table)
    if failed:
        raise typer.Exit(1)
