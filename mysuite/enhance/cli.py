from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import List, Optional

import typer

from rich.markup import escape
from mysuite import profiles as profiles_mod
from mysuite.profiles import ProfileError
from mysuite.config import MysuiteConfigError, load_config
from mysuite.enhance import history
from mysuite.enhance._parsing import (
    InvalidEnhanceInputError,
    check_no_output_collisions,
    output_path_for,
    resolve_input_files,
)
from mysuite.enhance.enhance import EnhanceError, EnhanceSettings, enhance_file
from mysuite.enhance.upscalers import backend_status
from mysuite.utils import jsonout
from mysuite.utils.console import WARNING, console, log_error, log_step

app = typer.Typer(
    help="Upscale and restore photos locally (classical backend always available; "
    "optional Real-ESRGAN/GFPGAN AI backend). No uploads, no watermark."
)


@app.command("run", help="Enhance photo(s), writing <name>_enhanced.<ext> beside each source.")
@jsonout.with_json("enhance")
def run(
    inputs: List[Path] = typer.Argument(
        ..., exists=True, readable=True,
        help="One or more photos, and/or directories (non-recursive unless --recursive).",
    ),
    preset: Optional[str] = typer.Option(
        None, "--preset", help="Settings bundle to start from (see `mysuite enhance presets`). "
        "Any other flag you pass overrides that field. Default: gentle.",
    ),
    scale: Optional[int] = typer.Option(None, "--scale", min=1, max=8, help="Upscale factor, 1-8."),
    denoise: Optional[float] = typer.Option(None, "--denoise", min=0.0, max=1.0, help="Denoise strength, 0-1."),
    sharpen: Optional[float] = typer.Option(None, "--sharpen", min=0.0, max=1.0, help="Sharpen strength, 0-1."),
    face_enhance: Optional[bool] = typer.Option(
        None, "--face-enhance/--no-face-enhance", help="Face restoration (needs the optional AI backend)."
    ),
    auto_white_balance: Optional[bool] = typer.Option(
        None, "--auto-white-balance/--no-auto-white-balance", help="Gray-world white balance."
    ),
    saturation: Optional[float] = typer.Option(None, "--saturation", min=0.0, max=3.0, help="1.0 = unchanged."),
    contrast: Optional[float] = typer.Option(None, "--contrast", min=0.0, max=3.0, help="1.0 = unchanged."),
    gamma: Optional[float] = typer.Option(None, "--gamma", min=0.2, max=3.0, help="1.0 = unchanged, <1 brightens."),
    restore_scratches: Optional[bool] = typer.Option(
        None, "--restore-scratches/--no-restore-scratches",
        help="Fill thin straight scratches/creases from their surroundings.",
    ),
    output_format: Optional[str] = typer.Option(None, "--format", help="png, jpg or webp."),
    output_quality: Optional[int] = typer.Option(None, "--quality", min=1, max=100, help="jpg/webp quality."),
    backend: str = typer.Option("auto", "--backend", help="auto, classical or realesrgan."),
    record_history: bool = typer.Option(
        False, "--record-history",
        help="Log each job (paths, sizes, timing) to a local database for `enhance history`. "
        "Off by default: a log of what you processed is a trace.",
    ),
    recursive: bool = typer.Option(False, "--recursive", "-r", help="Recurse into subdirectories."),
    overwrite: bool = typer.Option(False, "--overwrite/--no-overwrite", help="Overwrite existing outputs."),
    dry_run: bool = typer.Option(False, "--dry-run", help="List input -> output pairs, write nothing."),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Explicit path to mysuite.toml."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Print only the summary."),
) -> None:
    if backend not in ("auto", "classical", "realesrgan"):
        log_error(f"unknown backend: {backend!r} — expected auto, classical or realesrgan")
        raise typer.Exit(1)

    try:
        config = load_config(config_path)
        explicit = {
            "scale": scale, "denoise": denoise, "sharpen": sharpen, "face_enhance": face_enhance,
            "auto_white_balance": auto_white_balance, "saturation": saturation, "contrast": contrast,
            "gamma": gamma, "restore_scratches": restore_scratches,
            "output_format": output_format, "output_quality": output_quality,
        }
        active_profile = profiles_mod.resolve(config)
        if preset is None and active_profile and active_profile.enhance_preset:
            preset = active_profile.enhance_preset
        settings = EnhanceSettings.from_dict(config.resolve_enhance_settings(preset or "gentle", explicit))
        files = resolve_input_files(inputs, recursive=recursive)
        check_no_output_collisions(files, settings.output_format)
    except (MysuiteConfigError, EnhanceError, InvalidEnhanceInputError, ProfileError) as exc:
        log_error(str(exc))
        raise typer.Exit(1) from exc

    if dry_run:
        for f in files:
            jsonout.add_item(input=f, output=output_path_for(f, settings.output_format), status="planned")
            if not quiet:
                console.print(f"[dim]{escape(str(f))} -> {escape(str(output_path_for(f, settings.output_format)))}[/dim]")
        if not quiet:
            console.print(f"\n[dim]dry run — {len(files)} photo(s) planned, 0 written[/dim]")
        return

    written = skipped = failed = 0
    for f in files:
        try:
            outcome = enhance_file(f, settings, backend=backend, overwrite=overwrite)
        except EnhanceError as exc:
            failed += 1
            jsonout.add_item(input=f, status="failed", error=str(exc))
            log_error(f"{escape(str(f))}: {escape(str(exc))}")
            if record_history:
                history.record(input_path=str(f), output_path=None, preset=preset or "gentle",
                               backend=backend, status="failed", error=str(exc))
            continue

        jsonout.add_item(
            input=f, output=outcome.output_path,
            status="skipped_existing" if outcome.status == "skipped_existing" else "written",
            **({} if outcome.status == "skipped_existing" else {
                "input_size": list(outcome.input_size), "output_size": list(outcome.output_size),
                "backend": outcome.backend_used, "seconds": round(outcome.duration_seconds, 2),
                "notes": outcome.notes}),
        )
        for note in getattr(outcome, "notes", []) or []:
            jsonout.add_warning(f"{f}: {note}")
        if outcome.status == "skipped_existing":
            skipped += 1
            if not quiet:
                console.print(f"[dim]— exists, skipped: {escape(str(outcome.output_path))}[/dim]")
            continue

        written += 1
        if not quiet:
            log_step(f"{escape(str(outcome.output_path))}  [dim]{outcome.input_size[0]}x{outcome.input_size[1]} -> "
                     f"{outcome.output_size[0]}x{outcome.output_size[1]}, {outcome.backend_used}, "
                     f"{outcome.duration_seconds:.1f}s[/dim]")
            for note in outcome.notes:
                console.print(f"[{WARNING}]⚠[/{WARNING}] {note}")
        if record_history:
            history.record(
                input_path=str(f), output_path=str(outcome.output_path), preset=preset or "gentle",
                backend=outcome.backend_used, status="done", input_size=outcome.input_size,
                output_size=outcome.output_size, duration_seconds=outcome.duration_seconds,
            )

    if not quiet:
        console.print(
            f"\n[bold green]done[/bold green] — {written} written, {skipped} already existed "
            f"(use --overwrite to replace), {failed} failed"
        )
    if failed:
        raise typer.Exit(1)


@app.command("presets", help="List enhance presets (built-in and from mysuite.toml).")
def presets(
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Explicit path to mysuite.toml."),
) -> None:
    try:
        config = load_config(config_path)
    except MysuiteConfigError as exc:
        log_error(str(exc))
        raise typer.Exit(1) from exc
    for name in sorted(config.enhance_presets):
        settings = ", ".join(f"{k}={v}" for k, v in config.enhance_presets[name].items())
        console.print(f"[bold]{name}[/bold]  [dim]{settings}[/dim]")
    status = backend_status()
    console.print(
        "\n[dim]AI backend (Real-ESRGAN): "
        + ("available" if status["realesrgan_available"] else "not installed — classical backend only; see README")
        + "[/dim]"
    )


@app.command("history", help="Show jobs recorded with --record-history.")
def show_history(
    limit: int = typer.Option(50, "--limit", min=1),
    clear: bool = typer.Option(False, "--clear", help="Delete the history database."),
) -> None:
    if clear:
        console.print(f"cleared {history.clear()} recorded job(s)")
        return
    rows = history.list_jobs(limit=limit)
    if not rows:
        console.print("[dim]no recorded jobs (history is off unless you pass --record-history)[/dim]")
        return
    for r in rows:
        when = datetime.fromtimestamp(r.created_at).strftime("%Y-%m-%d %H:%M")
        out = Path(r.output_path).name if r.output_path else "-"
        console.print(f"#{r.id:<4} {when}  {r.status:<7} {r.preset or '-':<10} {escape(Path(r.input_path).name)} -> {escape(out)}")
