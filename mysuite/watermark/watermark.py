from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from mysuite.config import ToolPaths
from mysuite.convert._parsing import RASTER_FORMATS, detect_source_format
from mysuite.utils.subprocess_utils import atomic_write_via, run
from mysuite.watermark._parsing import output_path_for

_DEFAULT_DPI = 300.0

# User-facing position names -> ImageMagick's -gravity keywords.
GRAVITY_BY_POSITION: dict[str, str] = {
    "top-left": "NorthWest",
    "top-center": "North",
    "top-right": "NorthEast",
    "center-left": "West",
    "center": "Center",
    "center-right": "East",
    "bottom-left": "SouthWest",
    "bottom-center": "South",
    "bottom-right": "SouthEast",
}


class WatermarkError(RuntimeError):
    pass


@dataclass
class WatermarkOutcome:
    input_path: Path
    output_path: Path
    status: str  # "written" | "skipped_existing"


def _tmp_sibling(input_path: Path, suffix: str) -> Path:
    handle = tempfile.NamedTemporaryFile(
        suffix=suffix, prefix=f"mysuite-watermark-{input_path.stem}-", delete=False
    )
    handle.close()
    return Path(handle.name)


def _rasterize_to_png(input_path: Path, source_format: str, tools: ToolPaths) -> Path:
    """Same vector->PNG rasterization as mysuite.cutout.cutout — kept as an
    independent copy rather than a cross-module import of a "private" helper,
    matching how mysuite.convert.converter also keeps its own copy rather
    than reusing mysuite.export.renderer's."""
    tmp_png = _tmp_sibling(input_path, ".png")
    if source_format == "svg":
        run([
            tools.rsvg_convert, "--format=png", f"--dpi-x={_DEFAULT_DPI}", f"--dpi-y={_DEFAULT_DPI}",
            f"--output={tmp_png}", str(input_path),
        ])
    else:  # pdf or eps
        run([
            tools.gs, "-dNOPAUSE", "-dBATCH", "-dSAFER", "-sDEVICE=png16m",
            f"-r{_DEFAULT_DPI}", f"-sOutputFile={tmp_png}", str(input_path),
        ])
    return tmp_png


def _image_width(path: Path, tools: ToolPaths) -> int:
    result = run([tools.magick, "identify", "-format", "%w", str(path)])
    return int(result.stdout.strip())


def watermark_file(
    input_path: Path,
    *,
    logo_path: Path,
    tools: ToolPaths,
    position: str = "bottom-right",
    scale_pct: float = 15.0,
    opacity: int = 80,
    margin_pct: float = 3.0,
    overwrite: bool = False,
) -> WatermarkOutcome:
    """Composites logo_path onto input_path as a visible watermark, scaled to
    scale_pct% of the base image's width, positioned at one of the 9-point
    grid positions with margin_pct% breathing room from the edge, blended at
    opacity%. Raises WatermarkError for an unrecognized source/logo format or
    an unknown position, and MysuiteToolError if any tool invocation fails."""
    source_format = detect_source_format(input_path)
    if source_format is None:
        raise WatermarkError(f"unrecognized source format: {input_path}")

    logo_format = detect_source_format(logo_path)
    if logo_format is None:
        raise WatermarkError(f"unrecognized logo format: {logo_path}")

    if position not in GRAVITY_BY_POSITION:
        raise WatermarkError(
            f"unknown position {position!r} — expected one of {', '.join(GRAVITY_BY_POSITION)}"
        )

    output_path = output_path_for(input_path, source_format)

    if not overwrite and output_path.exists():
        return WatermarkOutcome(input_path, output_path, "skipped_existing")

    base_path = input_path
    is_temp_base = False
    if source_format not in RASTER_FORMATS:
        base_path = _rasterize_to_png(input_path, source_format, tools)
        is_temp_base = True

    logo_raster = logo_path
    is_temp_logo = False
    if logo_format not in RASTER_FORMATS:
        logo_raster = _rasterize_to_png(logo_path, logo_format, tools)
        is_temp_logo = True

    try:
        base_width = _image_width(base_path, tools)
        logo_width = max(int(base_width * scale_pct / 100), 1)
        margin_px = max(int(base_width * margin_pct / 100), 0)
        gravity = GRAVITY_BY_POSITION[position]

        def write(tmp_path: Path) -> None:
            run([
                tools.magick, "composite",
                "-gravity", gravity,
                "-geometry", f"{logo_width}x+{margin_px}+{margin_px}",
                "-dissolve", str(opacity),
                str(logo_raster), str(base_path), str(tmp_path),
            ])

        atomic_write_via(output_path, write)
    finally:
        if is_temp_base:
            base_path.unlink(missing_ok=True)
        if is_temp_logo:
            logo_raster.unlink(missing_ok=True)

    return WatermarkOutcome(input_path, output_path, "written")
