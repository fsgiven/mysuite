from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, UnidentifiedImageError

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
    notes: list[str] = field(default_factory=list)


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


# Short names people type, mapped to the nine positions above.
POSITION_ALIASES: dict[str, str] = {
    "top": "top-center", "bottom": "bottom-center", "left": "center-left", "right": "center-right",
    "middle": "center", "centre": "center", "top-centre": "top-center", "bottom-centre": "bottom-center",
    "centre-left": "center-left", "centre-right": "center-right",
}
POSITIONS: tuple[str, ...] = (*GRAVITY_BY_POSITION, *POSITION_ALIASES)
SIZE_BY = ("width", "height")


def resolve_position(position: str) -> str:
    """`bottom` -> `bottom-center` etc.; raises WatermarkError for an unknown name."""
    name = position.strip().lower()
    name = POSITION_ALIASES.get(name, name)
    if name not in GRAVITY_BY_POSITION:
        raise WatermarkError(
            f"unknown position {position!r} — expected one of {', '.join(GRAVITY_BY_POSITION)} "
            "(or top, bottom, left, right, middle)"
        )
    return name


def _base_info(path: Path) -> tuple[int, int, bool, int]:
    """(upright width, upright height, has alpha, frames) from the header, without decoding the pixels."""
    try:
        with Image.open(path) as im:
            w, h = im.size
            try:
                orientation = im.getexif().get(0x0112, 1)
            except Exception:  # noqa: BLE001
                orientation = 1
            alpha = im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info
            frames = getattr(im, "n_frames", 1)
    except (UnidentifiedImageError, OSError) as exc:
        raise WatermarkError(f"{path.name} is not an image mysuite can read (or it is damaged)") from exc
    if orientation in (5, 6, 7, 8):
        w, h = h, w
    return w, h, alpha, frames


def _logo_size(path: Path) -> tuple[int, int]:
    with Image.open(path) as im:
        return im.size


def watermark_file(
    input_path: Path,
    *,
    logo_path: Path,
    tools: ToolPaths,
    position: str = "bottom-right",
    scale_pct: float = 15.0,
    opacity: int = 80,
    margin_pct: float = 3.0,
    size_by: str = "width",
    quality: int | None = None,
    overwrite: bool = False,
) -> WatermarkOutcome:
    """Composites logo_path onto input_path as a visible watermark, in one ImageMagick pass.

    The logo is scale_pct% of the photo's width (or height, with ``size_by="height"``), placed at one of the
    nine positions (aliases like ``bottom`` work) with margin_pct% of the width as breathing room, blended at
    opacity%. The photo is turned upright (EXIF) and made sRGB colour first. A JPEG stays a JPEG with 4:2:0
    chroma subsampling and the source's own quality (or ``quality``). Only the first frame of an animation
    is stamped; the outcome's notes say so. Raises WatermarkError for an unrecognised source/logo/position,
    MysuiteToolError if a tool fails."""
    input_path = Path(os.path.abspath(input_path))
    logo_path = Path(os.path.abspath(logo_path))
    source_format = detect_source_format(input_path)
    if source_format is None:
        raise WatermarkError(f"unrecognized source format: {input_path}")

    logo_format = detect_source_format(logo_path)
    if logo_format is None:
        raise WatermarkError(f"unrecognized logo format: {logo_path}")

    position = resolve_position(position)
    if size_by not in SIZE_BY:
        raise WatermarkError(f"--size-by must be width or height, not {size_by!r}")

    output_path = output_path_for(input_path, source_format)

    if not overwrite and output_path.exists():
        return WatermarkOutcome(input_path, output_path, "skipped_existing")

    temps: list[Path] = []
    notes: list[str] = []
    try:
        base_path = input_path
        if source_format not in RASTER_FORMATS:
            base_path = _rasterize_to_png(input_path, source_format, tools)
            temps.append(base_path)
        width, height, has_alpha, frames = _base_info(base_path)
        if frames > 1:
            notes.append(f"{frames} frames in the source; only the first was watermarked")

        logo_raster = logo_path
        if logo_format not in RASTER_FORMATS:
            logo_raster = _rasterize_to_png(logo_path, logo_format, tools)
            temps.append(logo_raster)
        lw, lh = _logo_size(logo_raster)
        if size_by == "height":
            target_h = max(int(height * scale_pct / 100), 1)
            geometry = f"{max(round(lw * target_h / lh), 1)}x{target_h}!"
        else:
            target_w = max(int(width * scale_pct / 100), 1)
            geometry = f"{target_w}x{max(round(lh * target_w / lw), 1)}!"
        margin_px = max(int(width * margin_pct / 100), 0)
        gravity = GRAVITY_BY_POSITION[position]
        # The margin pushes away from an edge; on a centred axis it would only shift the logo off centre.
        dx = 0 if gravity in ("North", "South", "Center") else margin_px
        dy = 0 if gravity in ("West", "East", "Center") else margin_px

        ext = output_path.suffix.lower()
        if ext in (".jpg", ".jpeg"):
            # 4:2:0 like the camera; without -quality ImageMagick keeps the source JPEG's own quality.
            tail = ["-type", "TrueColor", "-sampling-factor", "2x2,1x1,1x1"]
            tail += ["-quality", str(quality)] if quality is not None else []
            token = "JPEG"
        elif ext == ".png":
            tail, token = ["-type", "TrueColorAlpha" if has_alpha else "TrueColor"], "PNG32" if has_alpha else "PNG24"
        else:
            tail = ["-quality", str(quality)] if quality is not None and ext == ".webp" else []
            token = ""

        def write(tmp_path: Path) -> None:
            run([
                tools.magick, f"{base_path}[0]", "-auto-orient", "-colorspace", "sRGB",
                "(", str(logo_raster), "-resize", geometry, ")",
                "-gravity", gravity, "-geometry", f"+{dx}+{dy}",
                "-compose", "dissolve", "-define", f"compose:args={opacity}", "-composite",
                *tail, f"{token}:{tmp_path}" if token else str(tmp_path),
            ])

        atomic_write_via(output_path, write, preserve_extension=True)
    finally:
        for temp in temps:
            temp.unlink(missing_ok=True)

    return WatermarkOutcome(input_path, output_path, "written", notes)
