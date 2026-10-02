from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

import typer
from rich.markup import escape

from mysuite.convert._parsing import InvalidInputError, RASTER_FORMATS, detect_source_format, resolve_input_files
from mysuite.mark import core
from mysuite.utils import jsonout
from mysuite.utils.console import console, log_error, log_skip, log_step

app = typer.Typer(help="Invisible ownership mark: embed it in your pictures, later check whether a picture carries it.", no_args_is_help=True)


def _key(key: Optional[str]) -> str:
    key = key or os.environ.get("MYSUITE_MARK_KEY")
    if not key:
        log_error("a key is needed: --key SECRET or the MYSUITE_MARK_KEY environment variable (keep it secret; without it nobody can check the mark)")
        raise typer.Exit(1)
    return key


def _files(inputs: List[Path], recursive: bool) -> list[Path]:
    try:
        files = [f for f in resolve_input_files(inputs, recursive=recursive) if detect_source_format(f) in RASTER_FORMATS]
    except InvalidInputError as exc:
        log_error(escape(str(exc)))
        raise typer.Exit(1) from exc
    if not files:
        log_error("no raster images found")
        raise typer.Exit(1)
    return files


@app.command("embed", help="Add your invisible mark. Output: <name>_marked.png beside the original.")
@jsonout.with_json("mark-embed")
def embed(
    inputs: List[Path] = typer.Argument(..., exists=True, readable=True, help="Images / folders."),
    key: Optional[str] = typer.Option(None, "--key", help="Your secret key (or MYSUITE_MARK_KEY). Same key = same mark."),
    ident: Optional[str] = typer.Option(None, "--id", help="Optional label mixed into the mark, e.g. a campaign or recipient: a different id is a different mark."),
    strength: str = typer.Option("standard", "--strength", help="subtle (~44 dB), standard (~40 dB), strong (~37 dB): stronger survives more, shows more."),
    output_format: str = typer.Option("png", "--format", help="png (best), tiff or jpeg."),
    quality: int = typer.Option(95, "--quality", help="JPEG quality."),
    recursive: bool = typer.Option(False, "--recursive", "-r"),
    overwrite: bool = typer.Option(False, "--overwrite/--no-overwrite"),
) -> None:
    secret = _key(key)
    failed = 0
    for f in _files(inputs, recursive):
        try:
            r = core.embed_file(f, key=secret, ident=ident, strength=strength, fmt=output_format, quality=quality, overwrite=overwrite)
        except core.MarkError as exc:
            failed += 1
            jsonout.add_item(input=f, status="failed", error=str(exc))
            log_error(f"{escape(str(f))}: {escape(str(exc))}")
            continue
        if r.status == "skipped_existing":
            jsonout.add_item(input=f, output=r.path, status="skipped_existing")
            console.print(f"[dim]— exists, skipped: {escape(str(r.path))}[/dim]")
            continue
        jsonout.add_item(input=f, output=r.path, status="written", psnr_db=r.psnr_db, strength=r.strength, notes=r.notes or [])
        log_step(f"{escape(str(r.path))}  [dim]{r.psnr_db} dB[/dim]")
        for n in r.notes or []:
            jsonout.add_warning(f"{f.name}: {n}")
            log_skip(escape(n))
    if failed:
        raise typer.Exit(1)


@app.command("detect", help="Check whether pictures carry YOUR mark. Exit code 1 if none of them does with --strict.")
@jsonout.with_json("mark-detect")
def detect(
    inputs: List[Path] = typer.Argument(..., exists=True, readable=True, help="Pictures to check."),
    key: Optional[str] = typer.Option(None, "--key", help="Your secret key (or MYSUITE_MARK_KEY)."),
    ident: Optional[str] = typer.Option(None, "--id", help="The id used when embedding (if any)."),
    recursive: bool = typer.Option(False, "--recursive", "-r"),
    strict: bool = typer.Option(False, "--strict", help="Exit 1 when a picture does NOT carry the mark."),
    deep: bool = typer.Option(False, "--deep", help="Also look for slightly rotated copies (±3°): much slower."),
) -> None:
    secret = _key(key)
    missing = failed = 0
    for f in _files(inputs, recursive):
        try:
            d = core.detect_file(f, key=secret, ident=ident, deep=deep)
        except core.MarkError as exc:
            failed += 1
            jsonout.add_item(input=f, status="failed", error=str(exc))
            log_error(f"{escape(str(f))}: {escape(str(exc))}")
            continue
        if d.scales_tried == 0:
            failed += 1
            jsonout.add_item(input=f, status="failed", error="the picture is too small to check")
            log_error(f"{escape(str(f))}: too small to check")
            continue
        jsonout.add_item(input=f, status="marked" if d.detected else "not_found", detected=d.detected, confidence_z=d.z,
                         scale=d.scale, mirrored=d.mirrored, angle=d.angle, threshold=core.DETECT_Z, width=d.width, height=d.height)
        if d.detected:
            console.print(f"[#4ADE80]✓ marked[/#4ADE80] {escape(str(f))}  [dim]z={d.z}, scale ~{d.scale:g}x{', mirrored' if d.mirrored else ''}{f', rotated {d.angle:g}°' if d.angle else ''}[/dim]")
        else:
            missing += 1
            console.print(f"[#FBBF24]✗ not found[/#FBBF24] {escape(str(f))}  [dim]z={d.z} (needs {core.DETECT_Z:g})[/dim]")
    jsonout.set_extra(threshold=core.DETECT_Z)
    if failed or (strict and missing):
        raise typer.Exit(1)
