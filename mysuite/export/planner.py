from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from mysuite.export.artwork import raster_dims
from mysuite.export.models import BundleJob, ExportJob, ExportPlan, SkipReason
from mysuite.export.naming import DEFAULT_BUNDLE_NAMING_TEMPLATE, NamingError, render_bundle_path, render_path
from mysuite.export.units import Margin, Size

# PNG/WebP/ICO/ICNS have no CMYK color mode, SVG color is defined in RGB/sRGB by
# spec, and CMYK JPEG has well-known viewer-compatibility issues — so CMYK is
# only meaningful for the print formats PDF/EPS and, since it's a true CMYK
# raster format, TIFF.
CMYK_CAPABLE_FORMATS = {"pdf", "eps", "tiff"}

# ico/icns pack multiple sizes into ONE output file, unlike every other format
# which writes one file per size.
BUNDLE_FORMATS = {"ico", "icns"}

# iconutil requires exactly this standard Apple .iconset size set to build a
# valid .icns — so icns always builds this fixed set, independent of --sizes.
ICNS_STANDARD_PIXEL_SIZES = [16, 32, 64, 128, 256, 512, 1024]

_NO_CMYK_REASONS = {
    "png": "PNG has no CMYK color type",
    "svg": "SVG color is defined in RGB/sRGB",
    "jpeg": "CMYK JPEG has well-known viewer-compatibility issues, so it isn't supported here",
    "webp": "WebP has no CMYK color mode",
    "ico": "ICO is a screen/display icon format with no CMYK concept",
    "icns": "ICNS is a screen/display icon format with no CMYK concept",
}


class MysuitePlannerError(RuntimeError):
    pass


def _confined(output_path: Path, out_dir: Path) -> Path:
    """Templates may create subfolders but must not climb out of the output folder
    (FINDING S2) — profiles and agents make config less trusted than it used to be."""
    root = os.path.normpath(os.path.abspath(out_dir))
    target = os.path.normpath(os.path.abspath(output_path))
    if target != root and not target.startswith(root + os.sep):
        raise MysuitePlannerError(
            f"naming/path template resolves outside the output folder: {output_path} — "
            "templates may not contain '..' or absolute paths"
        )
    return output_path


def build_plan(
    *,
    name: str,
    out_dir: Path,
    sizes: list[Size],
    formats: list[str],
    profiles: list[str],
    naming_template: str,
    path_template: str,
    bundle_naming_template: str = DEFAULT_BUNDLE_NAMING_TEMPLATE,
    variant: str = "",
    strict: bool = False,
    name_folder: bool = True,
    artwork: tuple[float, float] | None = None,
    margin_for: Callable[[int], Margin] | None = None,
) -> ExportPlan:
    """artwork = the source's own (width, height), for {width}/{height}; margin_for(pixels) = the raster margin."""
    try:
        return _build_plan(
            name=name, out_dir=out_dir, sizes=sizes, formats=formats, profiles=profiles,
            naming_template=naming_template, path_template=path_template,
            bundle_naming_template=bundle_naming_template, variant=variant, strict=strict,
            name_folder=name_folder, artwork=artwork, margin_for=margin_for,
        )
    except NamingError as exc:
        raise MysuitePlannerError(str(exc)) from exc


def _dims(artwork, size: Size, margin_for) -> tuple[int | None, int | None]:
    if artwork is None:
        return None, None
    return raster_dims(artwork, size, margin_for(size.pixels) if margin_for else None)


def _build_plan(
    *, name, out_dir, sizes, formats, profiles, naming_template, path_template, bundle_naming_template,
    variant, strict, name_folder, artwork, margin_for,
) -> ExportPlan:
    sizes, formats, profiles = (list(dict.fromkeys(x)) for x in (sizes, formats, profiles))   # 32,32 is one size
    jobs: list[ExportJob] = []
    bundle_jobs: list[BundleJob] = []
    skips: list[SkipReason] = []
    seen_skip_combos: set[tuple[str, str]] = set()

    for fmt in formats:
        for colorspace in profiles:
            if colorspace == "cmyk" and fmt not in CMYK_CAPABLE_FORMATS:
                combo = (fmt, colorspace)
                if combo not in seen_skip_combos:
                    seen_skip_combos.add(combo)
                    reason = _NO_CMYK_REASONS.get(fmt, f"{fmt} has no standard CMYK color support")
                    if strict:
                        raise MysuitePlannerError(
                            f"requested CMYK for {fmt}, which doesn't support it: {reason}"
                        )
                    skips.append(SkipReason(format=fmt, colorspace=colorspace, reason=reason))
                continue

            if fmt in BUNDLE_FORMATS:
                if fmt == "icns":
                    bundle_sizes = tuple(
                        Size(label=str(px), pixels=px) for px in ICNS_STANDARD_PIXEL_SIZES
                    )
                else:
                    bundle_sizes = tuple(sizes)
                output_path = _confined(render_bundle_path(
                    path_template,
                    bundle_naming_template,
                    out_dir=out_dir,
                    name=name,
                    format=fmt,
                    colorspace=colorspace,
                    variant=variant,
                    name_folder=name_folder,
                ), out_dir)
                bundle_jobs.append(
                    BundleJob(
                        sizes=bundle_sizes, format=fmt, colorspace=colorspace, output_path=output_path
                    )
                )
                continue

            for size in sizes:
                width, height = _dims(artwork, size, margin_for)
                output_path = _confined(render_path(
                    path_template,
                    naming_template,
                    out_dir=out_dir,
                    name=name,
                    size=size,
                    format=fmt,
                    colorspace=colorspace,
                    variant=variant,
                    width=width,
                    height=height,
                    name_folder=name_folder,
                ), out_dir)
                jobs.append(
                    ExportJob(size=size, format=fmt, colorspace=colorspace, output_path=output_path)
                )

    plan = ExportPlan(jobs=jobs, skips=skips, bundle_jobs=bundle_jobs)
    find_clashes([plan])
    return plan


def find_clashes(plans: list[ExportPlan]) -> None:
    """Two outputs of one run must never land on the same file (the second would silently be 'skipped' or replace the first)."""
    seen: dict[str, object] = {}
    for plan in plans:
        for job in [*plan.jobs, *plan.bundle_jobs]:
            key = os.path.normcase(os.path.abspath(job.output_path))
            if key in seen:
                raise MysuitePlannerError(
                    f"two outputs would be written to the same file {job.output_path} - add {{size}}, {{format}}, "
                    "{colorspace}, {variant} or {name} to the naming/path template so every file gets its own name"
                )
            seen[key] = job
