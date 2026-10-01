from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from mysuite.config import ToolPaths
from mysuite.convert._parsing import TARGET_EXTENSIONS, detect_source_format, output_path_for
from mysuite.utils.subprocess_utils import atomic_write_via, run

# ImageMagick's format-token prefix for each target format's write path
# (`token:path`) — mostly identical to the target format name, except jpeg.
_MAGICK_FORMAT_TOKENS = {
    "png": "png",
    "jpeg": "jpg",
    "webp": "webp",
    "tiff": "tiff",
    "bmp": "bmp",
    "gif": "gif",
    "ico": "ico",
    "pdf": "pdf",
    "eps": "eps",
}

# Raster targets with no alpha channel — a transparent source is flattened
# onto a background (white by default) rather than left to render as garbage.
_NO_ALPHA_TARGETS = {"jpeg", "bmp"}


class ConversionError(RuntimeError):
    pass


@dataclass
class ConvertOutcome:
    input_path: Path
    output_path: Path
    status: str  # "written" | "skipped_existing"


def _tmp_sibling(input_path: Path, suffix: str) -> Path:
    handle = tempfile.NamedTemporaryFile(
        suffix=suffix, prefix=f"mysuite-convert-{input_path.stem}-", delete=False
    )
    handle.close()
    return Path(handle.name)


def convert_file(
    input_path: Path,
    target_format: str,
    *,
    tools: ToolPaths,
    dpi: float = 300.0,
    quality: int | None = None,
    background: str | None = None,
    overwrite: bool = False,
) -> ConvertOutcome:
    """Converts input_path to target_format, writing the result beside the
    source (same folder, same stem, new extension). Raises ConversionError for
    an unsupported/undetectable format or a no-op same-format request, and
    MysuiteToolError if an external tool invocation fails."""
    if target_format not in TARGET_EXTENSIONS:
        raise ConversionError(f"unsupported target format: {target_format!r}")

    source_format = detect_source_format(input_path)
    if source_format is None:
        raise ConversionError(f"unrecognized source format: {input_path}")

    output_path = output_path_for(input_path, target_format)

    if (
        input_path.parent.resolve() == output_path.parent.resolve()
        and input_path.name.lower() == output_path.name.lower()
    ):
        raise ConversionError(
            f"target format is the same as the source ({source_format}) — nothing to do"
        )

    if not overwrite and output_path.exists():
        return ConvertOutcome(input_path, output_path, "skipped_existing")

    if source_format == "svg":
        _convert_from_svg(
            input_path, output_path, target_format, dpi, tools, quality=quality, background=background
        )
    elif source_format in ("pdf", "eps"):
        _convert_from_vector_doc(
            input_path, output_path, source_format, target_format, dpi, tools,
            quality=quality, background=background,
        )
    else:
        _raster_to_raster(input_path, output_path, target_format, tools, quality=quality, background=background)

    return ConvertOutcome(input_path, output_path, "written")


def _svg_to_pdf(input_path: Path, output_path: Path, dpi: float, tools: ToolPaths) -> None:
    def write(tmp: Path) -> None:
        run([
            tools.rsvg_convert, "--format=pdf", f"--dpi-x={dpi}", f"--dpi-y={dpi}",
            f"--output={tmp}", str(input_path),
        ])

    atomic_write_via(output_path, write)


def _svg_to_png(input_path: Path, output_path: Path, dpi: float, tools: ToolPaths) -> None:
    def write(tmp: Path) -> None:
        run([
            tools.rsvg_convert, "--format=png", f"--dpi-x={dpi}", f"--dpi-y={dpi}",
            f"--output={tmp}", str(input_path),
        ])

    atomic_write_via(output_path, write)


def _convert_from_svg(
    input_path: Path,
    output_path: Path,
    target_format: str,
    dpi: float,
    tools: ToolPaths,
    *,
    quality: int | None,
    background: str | None,
) -> None:
    if target_format == "pdf":
        _svg_to_pdf(input_path, output_path, dpi, tools)
    elif target_format == "png":
        _svg_to_png(input_path, output_path, dpi, tools)
    elif target_format == "eps":
        tmp_pdf = _tmp_sibling(input_path, ".pdf")
        try:
            _svg_to_pdf(input_path, tmp_pdf, dpi, tools)
            _pdf_to_eps(tmp_pdf, output_path, tools)
        finally:
            tmp_pdf.unlink(missing_ok=True)
    else:
        tmp_png = _tmp_sibling(input_path, ".png")
        try:
            _svg_to_png(input_path, tmp_png, dpi, tools)
            _raster_to_raster(tmp_png, output_path, target_format, tools, quality=quality, background=background)
        finally:
            tmp_png.unlink(missing_ok=True)


def _vector_doc_to_png(input_path: Path, output_path: Path, dpi: float, tools: ToolPaths) -> None:
    def write(tmp: Path) -> None:
        run([
            tools.gs, "-dNOPAUSE", "-dBATCH", "-dSAFER", "-sDEVICE=png16m",
            f"-r{dpi}", f"-sOutputFile={tmp}", str(input_path),
        ])

    atomic_write_via(output_path, write)


def _pdf_to_eps(input_path: Path, output_path: Path, tools: ToolPaths) -> None:
    def write(tmp: Path) -> None:
        run([
            tools.gs, "-dBATCH", "-dNOPAUSE", "-dSAFER", "-sDEVICE=eps2write",
            "-dCompatibilityLevel=1.4", f"-sOutputFile={tmp}", str(input_path),
        ])

    atomic_write_via(output_path, write)


def _eps_to_pdf(input_path: Path, output_path: Path, tools: ToolPaths) -> None:
    def write(tmp: Path) -> None:
        run([
            tools.gs, "-dBATCH", "-dNOPAUSE", "-dSAFER", "-sDEVICE=pdfwrite",
            "-dCompatibilityLevel=1.4", f"-sOutputFile={tmp}", str(input_path),
        ])

    atomic_write_via(output_path, write)


def _convert_from_vector_doc(
    input_path: Path,
    output_path: Path,
    source_format: str,
    target_format: str,
    dpi: float,
    tools: ToolPaths,
    *,
    quality: int | None,
    background: str | None,
) -> None:
    if target_format == "eps":
        _pdf_to_eps(input_path, output_path, tools)
    elif target_format == "pdf":
        _eps_to_pdf(input_path, output_path, tools)
    elif target_format == "png":
        _vector_doc_to_png(input_path, output_path, dpi, tools)
    else:
        tmp_png = _tmp_sibling(input_path, ".png")
        try:
            _vector_doc_to_png(input_path, tmp_png, dpi, tools)
            _raster_to_raster(tmp_png, output_path, target_format, tools, quality=quality, background=background)
        finally:
            tmp_png.unlink(missing_ok=True)


def _raster_to_raster(
    input_path: Path,
    output_path: Path,
    target_format: str,
    tools: ToolPaths,
    *,
    quality: int | None,
    background: str | None,
) -> None:
    def write(tmp: Path) -> None:
        ops: list[str] = []
        bg = background or ("white" if target_format in _NO_ALPHA_TARGETS else None)
        if bg:
            ops += ["-background", bg, "-flatten"]
        if quality is not None and target_format in ("jpeg", "webp"):
            ops += ["-quality", str(quality)]
        token = _MAGICK_FORMAT_TOKENS[target_format]
        run([tools.magick, str(input_path), *ops, f"{token}:{tmp}"])

    atomic_write_via(output_path, write)
