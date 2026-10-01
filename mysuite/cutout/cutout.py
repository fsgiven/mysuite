from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from mysuite.config import ToolPaths
from mysuite.convert._parsing import RASTER_FORMATS, detect_source_format
from mysuite.cutout._parsing import output_path_for
from mysuite.utils.subprocess_utils import atomic_write_via, run

# Fixed rasterization DPI for vector sources (svg/pdf/eps) — cutout has no
# --dpi flag of its own since Vision operates on whatever raster resolution
# it's given; 300 matches Export/Convert's own default.
_DEFAULT_DPI = 300.0


class CutoutError(RuntimeError):
    pass


@dataclass
class CutoutOutcome:
    input_path: Path
    output_path: Path
    status: str  # "written" | "skipped_existing"


def _tmp_sibling(input_path: Path, suffix: str) -> Path:
    handle = tempfile.NamedTemporaryFile(
        suffix=suffix, prefix=f"mysuite-cutout-{input_path.stem}-", delete=False
    )
    handle.close()
    return Path(handle.name)


def _rasterize_to_png(input_path: Path, source_format: str, tools: ToolPaths) -> Path:
    """Rasterizes a vector source (svg/pdf/eps) to a temp PNG the caller must
    delete — Vision needs a real raster image, it can't read vector formats."""
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


# Below this mean opacity the "cutout" is effectively blank: Vision found no subject
# but still exited 0 (FINDING K2).
_MIN_COVERAGE = 0.005

# The helper passes source metadata through; a derived image shouldn't carry where it
# was taken or the device's serial numbers (FINDING K1). Orientation, colour profile and
# ordinary camera settings are left alone.
_IDENTIFYING_TAGS = ["-GPS:all=", "-XMP:GPS*=", "-*SerialNumber=", "-OwnerName=", "-CameraOwnerName="]


def _check_not_empty(path: Path, tools: ToolPaths) -> None:
    result = run([tools.magick, str(path), "-alpha", "extract", "-format", "%[fx:mean]", "info:"])
    try:
        coverage = float(result.stdout.strip())
    except ValueError:
        return
    if coverage < _MIN_COVERAGE:
        raise CutoutError("no foreground subject found — the cutout would be empty, nothing written")


def _drop_identifying_metadata(path: Path, tools: ToolPaths) -> None:
    run([tools.exiftool, "-q", "-overwrite_original", *_IDENTIFYING_TAGS, str(path)])


def cutout_file(input_path: Path, *, tools: ToolPaths, overwrite: bool = False) -> CutoutOutcome:
    """Isolates the foreground subject of input_path (via the macOS Vision
    framework, through the compiled mysuite-cutout helper) and writes a
    transparent-background PNG beside it (name_cutout.png). Raises
    CutoutError for an unrecognized source format, and MysuiteToolError if
    rasterization or the cutout tool itself fails (including "no foreground
    subject found in image")."""
    source_format = detect_source_format(input_path)
    if source_format is None:
        raise CutoutError(f"unrecognized source format: {input_path}")

    output_path = output_path_for(input_path)

    if not overwrite and output_path.exists():
        return CutoutOutcome(input_path, output_path, "skipped_existing")

    raster_path = input_path
    is_temp_raster = False
    if source_format not in RASTER_FORMATS:
        raster_path = _rasterize_to_png(input_path, source_format, tools)
        is_temp_raster = True

    try:
        def write(tmp_path: Path) -> None:
            run([tools.cutout_tool, str(raster_path), str(tmp_path)])
            _check_not_empty(tmp_path, tools)
            _drop_identifying_metadata(tmp_path, tools)

        # preserve_extension: exiftool picks the writer from the extension (".png.tmp" fails)
        atomic_write_via(output_path, write, preserve_extension=True)
    finally:
        if is_temp_raster:
            raster_path.unlink(missing_ok=True)

    return CutoutOutcome(input_path, output_path, "written")
