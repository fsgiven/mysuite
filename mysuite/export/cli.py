from __future__ import annotations

import atexit
import shutil
import tempfile
from pathlib import Path
from typing import List, Optional

import typer
from rich.tree import Tree

from rich.markup import escape
from mysuite.config import DEFAULT_NAMING_TEMPLATE, DEFAULT_PATH_TEMPLATE, MysuiteConfigError, load_config
from mysuite.doctor import require_tools
from mysuite.export._parsing import (
    VALID_FORMATS,
    VALID_PROFILES,
    InvalidInputError,
    parse_csv,
    parse_sizes,
    resolve_input_files,
)
from mysuite.export.naming import resolve_naming_templates, resolve_path_template_for_variant
from mysuite.export.planner import MysuitePlannerError, build_plan
from mysuite.color.cmyk import CmykError, CmykSettings
from mysuite.color.svg import DEFAULT_TOLERANCE
from mysuite import profiles as profiles_mod
from mysuite.color import variants as variant_mod
from mysuite.profiles import ProfileError
from mysuite.color.variants import VariantError
from mysuite.export.recolor import InvalidRecolorError, apply_recolor, make_variant_svg, parse_recolor_list, resolve_token_refs
from mysuite.tokens.model import TokenError
from mysuite.tokens.context import build_context
from mysuite.export.renderer import Renderer
from mysuite.export.units import (
    VALID_UNITS,
    InvalidPaddingError,
    InvalidSizeError,
    Size,
    parse_padding,
    parse_size,
)
from mysuite.utils.subprocess_utils import MysuiteToolError
from mysuite.utils import jsonout
from mysuite.vector import imports as imp
from mysuite.utils.console import console, log_error, log_skip, log_step


def _parse_sizes(value: Optional[str], *, dpi: float, default_unit: str) -> Optional[list[Size]]:
    try:
        return parse_sizes(value, dpi=dpi, default_unit=default_unit)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _build_tree(name: str, jobs, skips, bundle_jobs) -> Tree:
    tree = Tree(f"[bold]{name}[/bold]")
    by_format: dict[str, dict[str, list[Size]]] = {}
    for job in jobs:
        by_format.setdefault(job.format, {}).setdefault(job.colorspace, []).append(job.size)

    for fmt in sorted(by_format):
        fmt_branch = tree.add(f"[cyan]{fmt}[/cyan]")
        for colorspace in sorted(by_format[fmt]):
            sizes = sorted(by_format[fmt][colorspace], key=lambda s: s.pixels)
            sizes_str = ", ".join(s.label for s in sizes)
            fmt_branch.add(f"[magenta]{colorspace}[/magenta]  ({sizes_str})")

    by_bundle_format: dict[str, dict[str, tuple]] = {}
    for bundle_job in bundle_jobs:
        by_bundle_format.setdefault(bundle_job.format, {})[bundle_job.colorspace] = bundle_job.sizes

    for fmt in sorted(by_bundle_format):
        fmt_branch = tree.add(f"[cyan]{fmt}[/cyan] [dim](one file, multiple sizes bundled)[/dim]")
        for colorspace in sorted(by_bundle_format[fmt]):
            sizes = sorted(by_bundle_format[fmt][colorspace], key=lambda s: s.pixels)
            sizes_str = ", ".join(s.label for s in sizes)
            fmt_branch.add(f"[magenta]{colorspace}[/magenta]  (contains {sizes_str})")

    for skip in skips:
        tree.add(f"[#FBBF24]⚠ skipped {skip.format}/{skip.colorspace}[/#FBBF24] — {escape(skip.reason)}")

    return tree


@jsonout.with_json("export")
def export(
    inputs: List[Path] = typer.Argument(
        ..., exists=True, readable=True,
        help="One or more SVG files (also .svgz, PDF, Illustrator .ai and EPS: converted to SVG first), and/or directories of SVGs (non-recursive unless --recursive).",
    ),
    sizes: Optional[str] = typer.Option(
        None, "--sizes", "-s",
        help="Comma-separated sizes: plain numbers are pixels, or suffix with mm/cm/in "
        "(e.g. 16,32,5cm,2in).",
    ),
    formats: Optional[str] = typer.Option(
        None, "--formats", "-f",
        help="Comma-separated: png,pdf,eps,svg,jpeg,webp,tiff,ico,icns "
        "(ico/icns bundle multiple sizes into one file).",
    ),
    profiles: Optional[str] = typer.Option(None, "--profiles", "-p", help="Comma-separated: rgb,cmyk"),
    dpi: Optional[float] = typer.Option(
        None, "--dpi", "--ppi",
        help="Pixels/dots per inch used to convert mm/cm/in sizes to pixels (default from config, "
        "usually 300).",
    ),
    unit: Optional[str] = typer.Option(
        None, "--unit",
        help="Default unit (px, mm, cm, or in) for sizes with no explicit suffix, e.g. --unit mm "
        "--sizes 50,80 means 50mm,80mm. Per-size suffixes still override this.",
    ),
    background: Optional[str] = typer.Option(
        None, "--background",
        help="Background color (e.g. white, #ffffff) to flatten onto for formats without "
        "transparency. JPEG defaults to white if unset; other formats stay transparent.",
    ),
    quality: Optional[int] = typer.Option(
        None, "--quality", min=0, max=100, help="JPEG/WebP compression quality (0-100)."
    ),
    padding: Optional[str] = typer.Option(
        None, "--padding", "--margin",
        help="Uniform safe-space margin around the artwork for raster formats, e.g. 20, 20px, or "
        "10%% of each size. No background color is forced — it stays transparent unless "
        "--background is also set. Vector formats (pdf/eps/svg) are unaffected. Overridden per "
        "side by --margin-top/-right/-bottom/-left.",
    ),
    margin_top: Optional[str] = typer.Option(None, "--margin-top", help="Top margin, overrides --margin for this side."),
    margin_right: Optional[str] = typer.Option(None, "--margin-right", help="Right margin, overrides --margin for this side."),
    margin_bottom: Optional[str] = typer.Option(None, "--margin-bottom", help="Bottom margin, overrides --margin for this side."),
    margin_left: Optional[str] = typer.Option(None, "--margin-left", help="Left margin, overrides --margin for this side."),
    png_compression: Optional[int] = typer.Option(
        None, "--png-compression", min=0, max=9,
        help="PNG zlib compression level (0-9). Lossless either way — trades encode time for file size.",
    ),
    out: Optional[Path] = typer.Option(None, "--out", "-o", help="Base output directory."),
    name: Optional[str] = typer.Option(
        None, "--name", help="Override base name (default: each input file's stem). Only valid with a single input."
    ),
    date_stamp: Optional[bool] = typer.Option(
        None, "--date-stamp/--no-date-stamp",
        help="Insert today's date (YYYYMMDD) into filenames, e.g. logo_512_20260819.png. Only "
        "applies to the tool's own default naming templates — a naming_template you've already "
        "customized is left alone (it can reference {date} itself instead).",
    ),
    variant: Optional[str] = typer.Option(
        None, "--variant",
        help="Label this run as a variant (e.g. negative, mono) — nests output under "
        "exports/<name>/<variant>/... alongside the primary exports/<name>/... export instead of "
        "getting its own top-level name folder.",
    ),
    recolor: Optional[List[str]] = typer.Option(
        None, "--recolor",
        help="Color substitution FROM=TO — hex (e.g. --recolor '#000000=#ffffff') or a named CSS "
        "color (e.g. --recolor 'white=black'). Repeatable, and each occurrence may itself hold "
        "several space/comma-separated pairs, e.g. --recolor '#000=#fff white=black'. Applied to "
        "the SVG source before rendering.",
    ),
    recolor_tolerance: float = typer.Option(
        DEFAULT_TOLERANCE, "--recolor-tolerance", min=0.0,
        help="How close (CIEDE2000 colour difference) a colour must be to a --recolor FROM to count "
        "as it; 0 = exact match only, 2 = just-noticeable difference (default). Catches "
        "anti-aliased or slightly-off brand colours.",
    ),
    cmyk_mode: Optional[str] = typer.Option(
        None, "--cmyk-mode",
        help="How CMYK numbers are chosen for --profiles cmyk (pdf/eps/tiff all use the same engine): "
        "'exact' keeps the profile's values; 'clean' or 'clean:N' snaps every channel to a multiple "
        "of N (default 5, so 73/92 becomes 75/90) and prints the colour shift. Greys are always K-only.",
    ),
    cmyk_profile: Optional[Path] = typer.Option(
        None, "--cmyk-profile", exists=True, dir_okay=False,
        help="CMYK ICC profile (e.g. FOGRA39 or your printer's) used for the conversion and embedded "
        "as the PDF output intent. Default: Ghostscript's SWOP profile.",
    ),
    tokens: Optional[str] = typer.Option(
        None, "--tokens",
        help="Design tokens: a .css/.json file or folder, git+https://host/org/repo@REF#path=dir (pinned, cached), or "
        "figma:FILEKEY. Lets --recolor use token:NAME and enables the negative variant.",
    ),
    brand: Optional[str] = typer.Option(None, "--brand", help="Which brand of the token source (when it has several)."),
    theme: Optional[str] = typer.Option(None, "--theme", help="Which theme's token values --recolor token:NAME uses: light (default) or dark."),
    variants: Optional[str] = typer.Option(
        None, "--variants",
        help="Several colour variants in one run, each in its own folder: default, negative (token-driven, needs --tokens), "
        "invert, mono-black, mono-white, grayscale. E.g. default,negative,mono-white.",
    ),
    negative_map: Optional[List[str]] = typer.Option(
        None, "--negative-map",
        help="For the negative variant: LOGOCOLOUR=token:NAME makes that logo colour take the token's DARK value, e.g. "
        "'#1d1d1b=token:--headline-text-color'. Repeatable. Use it when several tokens share a colour but differ in dark mode.",
    ),
    negative_tolerance: float = typer.Option(
        5.0, "--negative-tolerance", min=0.0,
        help="How close (CIEDE2000) a logo colour must be to a token's light value for the negative variant to use that token.",
    ),
    tokens_refresh: bool = typer.Option(False, "--tokens-refresh", help="Fetch a git token source again instead of using the cached copy."),
    recursive: bool = typer.Option(
        False, "--recursive", help="When an input is a directory, include SVGs in subdirectories too."
    ),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Explicit path to mysuite.toml."),
    preset: Optional[str] = typer.Option(
        None, "--preset",
        help="Name of a [presets.NAME] block to apply, or a built-in preset (favicon, macos-icon).",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview the output file tree without writing anything."),
    strict: Optional[bool] = typer.Option(None, "--strict", help="Turn CMYK-unsupported-format warnings into hard errors."),
    overwrite: Optional[bool] = typer.Option(None, "--overwrite/--no-overwrite", help="Overwrite existing output files."),
    normalize_png: Optional[bool] = typer.Option(None, "--normalize-png/--no-normalize-png", help="Tag PNG output with sRGB via ImageMagick."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress per-file progress, print summary only."),
) -> None:
    try:
        input_files = resolve_input_files(inputs, recursive=recursive)
    except InvalidInputError as exc:
        log_error(str(exc))
        raise typer.Exit(1) from exc

    if name is not None and len(input_files) > 1:
        log_error("--name can only be used with a single input file")
        raise typer.Exit(1)

    try:
        config = load_config(config_path)
        active_profile = profiles_mod.resolve(config)
        settings = config.resolve_export_settings(preset, active_profile.export if active_profile else None)
    except (MysuiteConfigError, ProfileError) as exc:
        log_error(escape(str(exc)))
        raise typer.Exit(1) from exc
    if active_profile:                      # flags > preset > profile: only fill what the command line left open
        tokens = tokens or active_profile.tokens
        brand = brand or active_profile.brand
        theme = theme or active_profile.theme
        variants = variants or active_profile.variants
        negative_map = negative_map or (active_profile.negative_map or None)
        jsonout.set_extra(profile=active_profile.name)
    theme = theme or "light"

    resolved_dpi = settings.dpi if dpi is None else dpi
    resolved_unit = settings.default_unit if unit is None else unit
    if resolved_unit not in VALID_UNITS:
        log_error(f"unknown unit {resolved_unit!r} — expected one of {', '.join(sorted(VALID_UNITS))}")
        raise typer.Exit(1)
    try:
        resolved_sizes = _parse_sizes(sizes, dpi=resolved_dpi, default_unit=resolved_unit) or [
            parse_size(s, dpi=resolved_dpi, default_unit=resolved_unit) for s in settings.sizes
        ]
    except InvalidSizeError as exc:
        log_error(str(exc))
        raise typer.Exit(1) from exc
    resolved_formats = parse_csv(formats) or settings.formats
    resolved_profiles = parse_csv(profiles) or settings.profiles
    resolved_out = out or Path(settings.out_dir)
    resolved_strict = settings.strict if strict is None else strict
    resolved_overwrite = settings.overwrite if overwrite is None else overwrite
    resolved_normalize_png = settings.normalize_png if normalize_png is None else normalize_png
    resolved_background = settings.background if background is None else background
    resolved_quality = settings.quality if quality is None else quality
    resolved_padding = settings.padding if padding is None else padding
    resolved_png_compression = settings.png_compression if png_compression is None else png_compression
    resolved_date_stamp = settings.date_stamp if date_stamp is None else date_stamp
    effective_naming_template, effective_bundle_naming_template = resolve_naming_templates(
        settings.naming_template,
        settings.bundle_naming_template,
        date_stamp=resolved_date_stamp,
        default_naming_template=DEFAULT_NAMING_TEMPLATE,
    )

    try:
        resolved_cmyk = CmykSettings.parse(
            cmyk_mode or settings.cmyk_mode, str(cmyk_profile) if cmyk_profile else settings.cmyk_profile
        )
    except CmykError as exc:
        log_error(str(exc))
        raise typer.Exit(1) from exc
    resolved_recolor_tolerance = settings.recolor_tolerance if recolor_tolerance == DEFAULT_TOLERANCE else recolor_tolerance

    resolved_variant = settings.variant if variant is None else variant
    effective_path_template = resolve_path_template_for_variant(
        settings.path_template, variant=resolved_variant, default_path_template=DEFAULT_PATH_TEMPLATE
    )

    token_set = None
    token_ctx = None
    if tokens:
        try:
            token_ctx = build_context(tokens, brand, negative_map, tokens_refresh)
        except TokenError as exc:
            log_error(escape(str(exc)))
            raise typer.Exit(1) from exc
        token_set = token_ctx.tokens
        jsonout.set_extra(tokens={"source": token_ctx.loaded.description, "commit": token_ctx.loaded.commit, "count": len(token_set), "theme": theme})
    try:
        resolved_recolor_map = parse_recolor_list(recolor) if recolor else dict(settings.recolor)
        if any(str(v).startswith("token:") or str(k).startswith("token:") for k, v in resolved_recolor_map.items()):
            if token_set is None:
                raise InvalidRecolorError("token: references need --tokens SOURCE")
            resolved_recolor_map = resolve_token_refs(resolved_recolor_map, token_set, brand, theme)
    except (InvalidRecolorError, TokenError) as exc:
        log_error(escape(str(exc)))
        raise typer.Exit(1) from exc

    def _resolve_margin_side(cli_value: Optional[str], settings_value: Optional[str]) -> Optional[str]:
        if cli_value is not None:
            return cli_value
        if settings_value is not None:
            return settings_value
        return resolved_padding

    resolved_margin_top = _resolve_margin_side(margin_top, settings.margin_top)
    resolved_margin_right = _resolve_margin_side(margin_right, settings.margin_right)
    resolved_margin_bottom = _resolve_margin_side(margin_bottom, settings.margin_bottom)
    resolved_margin_left = _resolve_margin_side(margin_left, settings.margin_left)

    for side_name, side_value in [
        ("--margin-top", resolved_margin_top),
        ("--margin-right", resolved_margin_right),
        ("--margin-bottom", resolved_margin_bottom),
        ("--margin-left", resolved_margin_left),
    ]:
        if side_value is not None:
            try:
                parse_padding(side_value, reference_px=100)  # syntax check only; actual
                # px is resolved per-size at render time since "%" depends on each size.
            except InvalidPaddingError as exc:
                log_error(f"{side_name}: {escape(str(exc))}")
                raise typer.Exit(1) from exc

    unknown_formats = set(resolved_formats) - VALID_FORMATS
    if unknown_formats:
        log_error(f"unknown format(s): {', '.join(sorted(unknown_formats))}")
        raise typer.Exit(1)
    unknown_profiles = set(resolved_profiles) - VALID_PROFILES
    if unknown_profiles:
        log_error(f"unknown profile(s): {', '.join(sorted(unknown_profiles))}")
        raise typer.Exit(1)

    if resolved_out.exists() and not resolved_out.is_dir():
        log_error(f"output path is a file, not a folder: {escape(str(resolved_out))}")
        raise typer.Exit(1)

    require_tools(config.tools, "export")

    if "icns" in resolved_formats and shutil.which(config.tools.iconutil) is None:
        log_error(
            f"icns requested but {config.tools.iconutil!r} was not found on PATH — "
            "icns export requires macOS's built-in iconutil (not available on this system)."
        )
        raise typer.Exit(1)

    renderer = Renderer(
        config.tools,
        dpi=resolved_dpi,
        normalize_png=resolved_normalize_png,
        overwrite=resolved_overwrite,
        background=resolved_background,
        quality=resolved_quality,
        margin_top=resolved_margin_top,
        margin_right=resolved_margin_right,
        margin_bottom=resolved_margin_bottom,
        margin_left=resolved_margin_left,
        png_compression=resolved_png_compression,
        cmyk=resolved_cmyk,
    )

    variant_names = [v.strip().lower() for v in (variants or "default").split(",") if v.strip()] or ["default"]
    bad_variants = [v for v in variant_names if v not in variant_mod.ALL]
    if bad_variants or len(set(variant_names)) != len(variant_names):
        log_error("variants must be unique and from: " + ", ".join(variant_mod.ALL) + (f" (got {', '.join(bad_variants)})" if bad_variants else ""))
        raise typer.Exit(1)
    if variants and resolved_variant:
        log_error("use either --variant (one folder name) or --variants (a list of colour variants), not both")
        raise typer.Exit(1)
    if theme not in ("light", "dark"):
        log_error("--theme must be light or dark")
        raise typer.Exit(1)
    label_for = {v: (resolved_variant or "") if v == "default" else v for v in variant_names}
    path_templates = {
        v: resolve_path_template_for_variant(settings.path_template, variant=label_for[v] or None, default_path_template=DEFAULT_PATH_TEMPLATE)
        for v in variant_names
    }
    token_view = token_ctx.view if token_ctx else None
    negative_overrides = token_ctx.overrides if token_ctx else {}
    if token_view is None and any(v in variant_mod.TOKEN_DRIVEN for v in variant_names):
        log_error("the negative variant is driven by design tokens: pass --tokens SOURCE (or use invert / mono-white)")
        raise typer.Exit(1)

    batch = len(input_files) > 1
    total_written = 0
    total_skipped_existing = 0
    total_planned = 0

    converted: dict[Path, Path] = {}
    import_dir: Path | None = None
    for input_svg in input_files:
        resolved_name = name or input_svg.stem
        for variant_name in variant_names:

            try:
                plan = build_plan(
                    name=resolved_name,
                    out_dir=resolved_out,
                    sizes=resolved_sizes,
                    formats=resolved_formats,
                    profiles=resolved_profiles,
                    naming_template=effective_naming_template,
                    path_template=path_templates[variant_name],
                    bundle_naming_template=effective_bundle_naming_template,
                    variant=label_for[variant_name],
                    strict=resolved_strict,
                )
            except MysuitePlannerError as exc:
                log_error(f"{escape(str(input_svg))}: {escape(str(exc))}")
                raise typer.Exit(1) from exc

            if not quiet:
                if batch:
                    console.print(f"\n[bold]=== {escape(str(input_svg))} ===[/bold]")
                console.print(_build_tree(resolved_name, plan.jobs, plan.skips, plan.bundle_jobs))

            file_total = len(plan.jobs) + len(plan.bundle_jobs)
            total_planned += file_total

            for job in [*plan.jobs, *plan.bundle_jobs]:
                if dry_run:
                    jsonout.add_item(
                        input=input_svg, output=job.output_path, format=job.format, variant=label_for[variant_name],
                        colorspace=job.colorspace, status="planned",
                        **({"size": job.size.label} if hasattr(job, "size") else {"sizes": [s.label for s in job.sizes]}),
                    )
            for skip in plan.skips:
                jsonout.add_warning(f"{skip.format}/{skip.colorspace} skipped: {skip.reason}")

            if dry_run:
                if not quiet:
                    console.print(f"\n[dim]dry run — {file_total} file(s) would be written, 0 written[/dim]")
                continue

            for skip in plan.skips:
                if quiet:
                    continue
                log_skip(f"{skip.format}/{skip.colorspace} — {escape(skip.reason)}")

            def on_job_done(job, skipped: bool, input_svg=input_svg, variant_name=variant_name) -> None:
                jsonout.add_item(
                    input=input_svg, output=job.output_path, format=job.format, colorspace=job.colorspace, variant=label_for[variant_name],
                    status="skipped_existing" if skipped else "written",
                    **({"size": job.size.label} if hasattr(job, "size") else {"sizes": [s.label for s in job.sizes]}),
                )
                if quiet:
                    return
                if skipped:
                    console.print(f"[dim]— exists, skipped: {escape(str(job.output_path))}[/dim]")
                else:
                    log_step(str(job.output_path))

            if input_svg.suffix.lower() in imp.IMPORT_EXTENSIONS:          # PDF / AI / EPS / SVGZ: converted to SVG once, then treated as one
                if input_svg not in converted:
                    try:
                        if import_dir is None:
                            import_dir = Path(tempfile.mkdtemp(prefix="mysuite-export-import-"))
                            atexit.register(shutil.rmtree, import_dir, True)
                        require_tools(config.tools, "svg-import") if input_svg.suffix.lower() != ".svgz" else None
                        converted[input_svg] = imp.to_svg(input_svg, import_dir / str(len(converted)), tools=config.tools).svg
                    except (imp.ImportError_, MysuiteToolError) as exc:
                        log_error(f"{escape(str(input_svg))}: {escape(str(exc))}")
                        raise typer.Exit(1) from exc
                source_svg = converted[input_svg]
            else:
                source_svg = input_svg
            render_svg, is_temp = apply_recolor(source_svg, resolved_recolor_map, resolved_recolor_tolerance)
            if variant_name != "default":
                try:
                    render_svg, is_temp, variant_notes = make_variant_svg(render_svg, is_temp, variant_name, token_view, overrides=negative_overrides, tolerance=negative_tolerance)
                except (VariantError, TokenError) as exc:
                    log_error(f"{escape(str(input_svg))}: {escape(str(exc))}")
                    raise typer.Exit(1) from exc
                for note in variant_notes:
                    jsonout.add_warning(f"{variant_name}: {note}")
                    if not quiet:
                        log_skip(escape(f"{variant_name}: {note}"))
            try:
                result = renderer.execute(plan, render_svg, on_job_done=on_job_done)
            except CmykError as exc:
                log_error(f"{escape(str(input_svg))}: {escape(str(exc))}")
                raise typer.Exit(1) from exc
            except MysuiteToolError as exc:
                detail = (exc.stderr or "").strip().splitlines()
                log_error(f"{escape(str(input_svg))}: {escape(detail[-1] if detail else str(exc))}")
                raise typer.Exit(1) from exc
            except OSError as exc:
                log_error(f"{escape(str(input_svg))}: {escape(str(exc))}")
                raise typer.Exit(1) from exc
            finally:
                if is_temp:
                    render_svg.unlink(missing_ok=True)
            if result.cmyk_conversions and not quiet:
                console.print(
                    f"[dim]CMYK ({escape(resolved_cmyk.mode)}"
                    f"{':' + str(resolved_cmyk.step) if resolved_cmyk.mode == 'clean' else ''}, "
                    f"{escape(renderer._engine.description)}):[/dim]"
                )
                for conv in sorted(result.cmyk_conversions, key=lambda c: c.rgb):
                    console.print(f"[dim]  {escape(conv.label())}[/dim]")
            if result.cmyk_conversions:
                jsonout.set_extra(cmyk={
                    "mode": resolved_cmyk.mode, "step": resolved_cmyk.step if resolved_cmyk.mode == "clean" else None,
                    "profile": renderer._engine.description,
                    "colours": [
                        {"rgb": f"#{c.rgb[0]:02x}{c.rgb[1]:02x}{c.rgb[2]:02x}", "cmyk": list(c.cmyk),
                         "delta_e": c.delta_e, "neutral": c.neutral}
                        for c in sorted(result.cmyk_conversions, key=lambda c: c.rgb)
                    ],
                })
            for note in result.cmyk_notes:
                log_skip(escape(note))
            total_written += len(result.written)
            total_skipped_existing += len(result.skipped_existing)

            if not quiet and batch:
                console.print(
                    f"[bold green]done[/bold green] — {len(result.written)} written, "
                    f"{len(result.skipped_existing)} already existed"
                )

    if dry_run:
        if not quiet and batch:
            console.print(f"\n[dim]dry run — {len(input_files)} file(s), {total_planned} output file(s) total[/dim]")
        return

    if batch:
        console.print(
            f"\n[bold green]all done[/bold green] — {len(input_files)} input file(s), "
            f"{total_written} written, {total_skipped_existing} already existed "
            "(use --overwrite to replace)"
        )
    else:
        console.print(
            f"\n[bold green]done[/bold green] — {total_written} written, "
            f"{total_skipped_existing} already existed (use --overwrite to replace)"
        )
