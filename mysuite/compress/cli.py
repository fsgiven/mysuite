from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import typer

from rich.markup import escape
from mysuite.compress._parsing import (
    CODECS,
    InvalidCompressInputError,
    check_no_output_collisions,
    output_path_for,
)
from mysuite.compress.compress import CompressError, compress_file
from mysuite import profiles as profiles_mod
from mysuite.config import MysuiteConfigError, load_config
from mysuite.profiles import ProfileError
from mysuite.convert._parsing import InvalidInputError, resolve_input_files
from mysuite.doctor import require_tools
from mysuite.utils import jsonout
from mysuite.utils.console import console, log_error, log_step
from mysuite.utils.subprocess_utils import MysuiteToolError

# Which of the ToolPaths attrs a given codec actually needs — so `compress`
# only fails doctor-checks for tools relevant to the chosen codec, not the
# full 6-codec toolset.
_TOOL_ATTR_BY_CODEC: dict[str, str] = {
    "mozjpeg": "cjpeg",
    "webp": "cwebp",
    "avif": "avifenc",
    "oxipng": "oxipng",
    "pngquant": "pngquant",
    "gifsicle": "gifsicle",
}


@jsonout.with_json("compress")
def compress(
    inputs: Optional[List[Path]] = typer.Argument(
        None, exists=True, readable=True,
        help="One or more files, and/or directories, to compress (non-recursive unless --recursive). "
        "Not needed with --list-presets.",
    ),
    preset: Optional[str] = typer.Option(
        None, "--preset",
        help="Named settings bundle (codec + its parameters) to start from — "
        "run --list-presets to see them. Any other flag you also pass overrides "
        "that field of the preset; --codec becomes optional when --preset is given.",
    ),
    list_presets: bool = typer.Option(
        False, "--list-presets", help="List available compress presets and exit."
    ),
    codec: Optional[str] = typer.Option(
        None, "--codec",
        help=f"Target codec: {', '.join(CODECS)}. Required unless --preset supplies one.",
    ),
    quality: Optional[int] = typer.Option(
        None, "--quality", min=0, max=100,
        help="Codec quality, 0-100 (mozjpeg/webp/avif only — ignored by oxipng/gifsicle, use --quality-range for pngquant).",
    ),
    sharpen_amount: Optional[float] = typer.Option(
        None, "--sharpen-amount",
        help="Unsharp mask amount applied before encoding (e.g. 1.0). Unset = no sharpening.",
    ),
    sharpen_radius: Optional[float] = typer.Option(
        None, "--sharpen-radius", help="Unsharp mask radius (default 2.0)."
    ),
    sharpen_sigma: Optional[float] = typer.Option(
        None, "--sharpen-sigma", help="Unsharp mask sigma / blur amount (default 1.0)."
    ),
    sharpen_threshold: Optional[float] = typer.Option(
        None, "--sharpen-threshold",
        help="Unsharp mask threshold — minimum brightness change to sharpen (default 0.0).",
    ),
    # mozjpeg
    progressive: Optional[bool] = typer.Option(
        None, "--progressive/--baseline", help="[mozjpeg] Progressive vs. baseline JPEG encoding (default progressive)."
    ),
    subsample: Optional[str] = typer.Option(
        None, "--subsample", help="[mozjpeg] Chroma subsampling: 4:4:4 or 4:2:0 (default 4:2:0)."
    ),
    # webp
    lossless: Optional[bool] = typer.Option(
        None, "--lossless/--lossy", help="[webp] Lossless encoding instead of lossy (default lossy)."
    ),
    method: Optional[int] = typer.Option(
        None, "--method", min=0, max=6, help="[webp] Compression effort, 0-6, slower = smaller (default 6)."
    ),
    alpha_quality: Optional[int] = typer.Option(
        None, "--alpha-quality", min=0, max=100, help="[webp] Alpha channel quality, 0-100 (default 100)."
    ),
    # avif
    speed: Optional[int] = typer.Option(
        None, "--speed", min=0, max=10, help="[avif] Encoder speed, 0-10, slower = smaller (default 6)."
    ),
    # oxipng
    effort: Optional[int] = typer.Option(
        None, "--effort", min=0, max=6, help="[oxipng] Optimization effort, 0-6 (default 4)."
    ),
    interlace: Optional[bool] = typer.Option(
        None, "--interlace/--no-interlace", help="[oxipng] Apply Adam7 interlacing (default off)."
    ),
    # pngquant
    quality_range: Optional[str] = typer.Option(
        None, "--quality-range", help="[pngquant] min-max quality range, e.g. 65-90 (default 65-90)."
    ),
    pngquant_speed: Optional[int] = typer.Option(
        None, "--pngquant-speed", min=1, max=11,
        help="[pngquant] Speed/quality trade-off, 1 (slow) - 11 (fast) (default 4).",
    ),
    dither: Optional[bool] = typer.Option(
        None, "--dither/--no-dither", help="[pngquant] Floyd-Steinberg dithering (default on)."
    ),
    # gifsicle
    optimize_level: Optional[int] = typer.Option(
        None, "--optimize-level", min=1, max=3, help="[gifsicle] Optimization level, 1-3 (default 3)."
    ),
    lossy: Optional[int] = typer.Option(
        None, "--lossy", min=0, max=200, help="[gifsicle] Lossy compression amount, 0-200 (default 0)."
    ),
    recursive: bool = typer.Option(
        False, "--recursive", "-r", help="Recurse into subdirectories for folder inputs."
    ),
    overwrite: bool = typer.Option(
        False, "--overwrite/--no-overwrite", help="Overwrite existing output files."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Preview input -> output pairs without compressing."
    ),
    config_path: Optional[Path] = typer.Option(
        None, "--config", "-c", help="Explicit path to mysuite.toml."
    ),
    quiet: bool = typer.Option(
        False, "--quiet", "-q", help="Suppress per-file progress, print summary only."
    ),
) -> None:
    try:
        config = load_config(config_path)
    except MysuiteConfigError as exc:
        log_error(str(exc))
        raise typer.Exit(1) from exc

    if list_presets:
        for name in sorted(config.compress_presets):
            settings = config.compress_presets[name]
            settings_str = ", ".join(f"{k}={v}" for k, v in settings.items())
            console.print(f"[bold]{name}[/bold]  [dim]{settings_str}[/dim]")
        return

    if not inputs:
        log_error("no input files given — pass one or more files/folders (or use --list-presets)")
        raise typer.Exit(1)

    try:
        input_files = resolve_input_files(inputs, recursive=recursive)
    except InvalidInputError as exc:
        log_error(str(exc))
        raise typer.Exit(1) from exc

    try:
        active_profile = profiles_mod.resolve(config)
    except ProfileError as exc:
        log_error(escape(str(exc)))
        raise typer.Exit(1) from exc
    if preset is None and active_profile and active_profile.compress_preset:
        preset = active_profile.compress_preset

    explicit = {
        "codec": codec, "quality": quality,
        "sharpen_amount": sharpen_amount, "sharpen_radius": sharpen_radius,
        "sharpen_sigma": sharpen_sigma, "sharpen_threshold": sharpen_threshold,
        "progressive": progressive, "subsample": subsample,
        "lossless": lossless, "method": method, "alpha_quality": alpha_quality,
        "speed": speed,
        "effort": effort, "interlace": interlace,
        "quality_range": quality_range, "pngquant_speed": pngquant_speed, "dither": dither,
        "optimize_level": optimize_level, "lossy": lossy,
    }
    try:
        settings = config.resolve_compress_settings(preset, explicit)
    except MysuiteConfigError as exc:
        log_error(str(exc))
        raise typer.Exit(1) from exc

    resolved_codec = settings.pop("codec", None)
    if resolved_codec is None:
        log_error("--codec is required (or pass --preset to supply one)")
        raise typer.Exit(1)
    if resolved_codec not in CODECS:
        log_error(f"unknown codec: {resolved_codec!r} — expected one of {', '.join(CODECS)}")
        raise typer.Exit(1)

    require_tools(config.tools, f"compress-{resolved_codec}")

    try:
        check_no_output_collisions(input_files, resolved_codec)
    except InvalidCompressInputError as exc:
        log_error(str(exc))
        raise typer.Exit(1) from exc

    if dry_run:
        for input_path in input_files:
            output_path = output_path_for(input_path, resolved_codec)
            jsonout.add_item(input=input_path, output=output_path, status="planned")
            if not quiet:
                console.print(f"[dim]{escape(str(input_path))} -> {escape(str(output_path))}[/dim]")
        if not quiet:
            console.print(f"\n[dim]dry run — {len(input_files)} compression(s) planned, 0 written[/dim]")
        return

    total_written = 0
    total_skipped = 0
    failures: list[tuple[Path, str]] = []

    for input_path in input_files:
        try:
            outcome = compress_file(
                input_path, resolved_codec,
                tools=config.tools, overwrite=overwrite, **settings,
            )
        except (CompressError, MysuiteToolError) as exc:
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
