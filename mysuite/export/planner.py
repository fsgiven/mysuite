from __future__ import annotations

import os

from pathlib import Path

from mysuite.export.models import BundleJob, ExportJob, ExportPlan, SkipReason
from mysuite.export.naming import DEFAULT_BUNDLE_NAMING_TEMPLATE, render_bundle_path, render_path
from mysuite.export.units import Size

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
) -> ExportPlan:
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
                ), out_dir)
                bundle_jobs.append(
                    BundleJob(
                        sizes=bundle_sizes, format=fmt, colorspace=colorspace, output_path=output_path
                    )
                )
                continue

            for size in sizes:
                output_path = _confined(render_path(
                    path_template,
                    naming_template,
                    out_dir=out_dir,
                    name=name,
                    size=size,
                    format=fmt,
                    colorspace=colorspace,
                    variant=variant,
                ), out_dir)
                jobs.append(
                    ExportJob(size=size, format=fmt, colorspace=colorspace, output_path=output_path)
                )

    return ExportPlan(jobs=jobs, skips=skips, bundle_jobs=bundle_jobs)
