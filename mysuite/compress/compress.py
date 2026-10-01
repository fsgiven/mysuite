from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from mysuite.compress._parsing import CODECS, output_path_for
from mysuite.config import ToolPaths
from mysuite.convert._parsing import detect_source_format
from mysuite.utils.subprocess_utils import atomic_write_via, run

_DEFAULT_DPI = 300.0

# Which raw file extensions each codec's own encoder accepts natively —
# anything else gets converted first. mozjpeg's cjpeg is a traditional IJG
# encoder: it only accepts uncompressed PPM, never JPEG/PNG/etc. directly.
_NATIVE_INPUT_EXTENSIONS: dict[str, set[str]] = {
    "mozjpeg": set(),
    "webp": {".png", ".jpg", ".jpeg"},  # TIFF goes via magick: cwebp chokes on CMYK (Q2)
    "avif": {".png", ".jpg", ".jpeg"},
    "oxipng": {".png"},
    "pngquant": {".png"},
    "gifsicle": {".gif"},
}

# The extension to convert a non-native source into, per codec.
_PREPARE_EXTENSION: dict[str, str] = {
    "mozjpeg": ".ppm",
    "webp": ".png",
    "avif": ".png",
    "oxipng": ".png",
    "pngquant": ".png",
    "gifsicle": ".gif",
}


class CompressError(RuntimeError):
    pass


@dataclass
class CompressOutcome:
    input_path: Path
    output_path: Path
    status: str  # "written" | "skipped_existing"


def _tmp_sibling(input_path: Path, suffix: str) -> Path:
    handle = tempfile.NamedTemporaryFile(
        suffix=suffix, prefix=f"mysuite-compress-{input_path.stem}-", delete=False
    )
    handle.close()
    return Path(handle.name)


def _rasterize_to_png(input_path: Path, source_format: str, tools: ToolPaths) -> Path:
    """Same vector->PNG rasterization as mysuite.cutout.cutout and
    mysuite.watermark.watermark — kept as an independent copy per this
    project's established precedent (each tool package owns its copy rather
    than importing another tool's "private" helper)."""
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


def _sharpen_to_png(
    input_path: Path,
    source_format: str,
    tools: ToolPaths,
    *,
    radius: float,
    sigma: float,
    amount: float,
    threshold: float,
) -> Path:
    """Applies an unsharp mask, producing a new temp PNG. Vector sources are
    rasterized first (via _rasterize_to_png); raster sources are sharpened
    directly."""
    source_png = input_path
    is_temp_source = False
    if source_format in ("svg", "pdf", "eps"):
        source_png = _rasterize_to_png(input_path, source_format, tools)
        is_temp_source = True

    tmp_out = _tmp_sibling(input_path, ".sharpened.png")
    try:
        run([
            tools.magick, str(source_png),
            "-unsharp", f"{radius}x{sigma}+{amount}+{threshold}",
            str(tmp_out),
        ])
    finally:
        if is_temp_source:
            source_png.unlink(missing_ok=True)
    return tmp_out


def _prepare_for_codec(
    input_path: Path, source_format: str, codec: str, tools: ToolPaths
) -> tuple[Path, bool]:
    """Ensures a file in a format codec's own encoder accepts natively is
    available, converting only when the source isn't already one — vector
    sources are rasterized first (rsvg-convert/gs), then (if needed)
    reformatted via magick; raster sources go straight through magick.
    Returns (path_to_use, is_temp) — callers must unlink the path when
    is_temp is True."""
    native_exts = _NATIVE_INPUT_EXTENSIONS[codec]
    if input_path.suffix.lower() in native_exts:
        return input_path, False

    prepare_ext = _PREPARE_EXTENSION[codec]
    if source_format in ("svg", "pdf", "eps"):
        png_path = _rasterize_to_png(input_path, source_format, tools)
        if prepare_ext == ".png":
            return png_path, True
        tmp_out = _tmp_sibling(input_path, prepare_ext)
        try:
            run([tools.magick, str(png_path), str(tmp_out)])
        finally:
            png_path.unlink(missing_ok=True)
        return tmp_out, True

    tmp_out = _tmp_sibling(input_path, prepare_ext)
    run([tools.magick, f"{input_path}[0]", "-colorspace", "sRGB", str(tmp_out)])  # CMYK etc. -> RGB (Q2)
    return tmp_out, True


def _compress_mozjpeg(
    source: Path, output_path: Path, tools: ToolPaths, *,
    quality: int | None, progressive: bool, subsample: str,
) -> None:
    sample = "1x1" if subsample == "4:4:4" else "2x2"

    def write(tmp: Path) -> None:
        cmd = [tools.cjpeg, "-sample", sample, "-outfile", str(tmp)]
        if quality is not None:
            cmd += ["-quality", str(quality)]
        cmd += ["-progressive"] if progressive else ["-baseline"]
        cmd.append(str(source))
        run(cmd)

    atomic_write_via(output_path, write)


def _compress_webp(
    source: Path, output_path: Path, tools: ToolPaths, *,
    quality: int | None, lossless: bool, method: int, alpha_quality: int,
) -> None:
    def write(tmp: Path) -> None:
        cmd = [tools.cwebp, "-m", str(method), "-alpha_q", str(alpha_quality)]
        if lossless:
            cmd.append("-lossless")
        cmd += ["-q", str(quality if quality is not None else 75)]
        cmd += [str(source), "-o", str(tmp)]
        run(cmd)

    atomic_write_via(output_path, write)


def _compress_avif(
    source: Path, output_path: Path, tools: ToolPaths, *,
    quality: int | None, speed: int,
) -> None:
    def write(tmp: Path) -> None:
        cmd = [tools.avifenc, "-s", str(speed)]
        if quality is not None:
            cmd += ["-q", str(quality)]
        cmd += [str(source), str(tmp)]
        run(cmd)

    atomic_write_via(output_path, write)


def _compress_oxipng(
    source: Path, output_path: Path, tools: ToolPaths, *,
    effort: int, interlace: bool,
) -> None:
    def write(tmp: Path) -> None:
        cmd = [tools.oxipng, "-o", str(effort), "--strip", "safe"]
        cmd += ["-i", "on" if interlace else "off"]
        cmd += [str(source), "--out", str(tmp)]
        run(cmd)

    atomic_write_via(output_path, write)


def _compress_pngquant(
    source: Path, output_path: Path, tools: ToolPaths, *,
    quality_range: str, speed: int, dither: bool,
) -> None:
    def write(tmp: Path) -> None:
        cmd = [
            tools.pngquant, f"--quality={quality_range}", "--speed", str(speed),
            "--force",
        ]
        if not dither:
            cmd.append("--nofs")
        cmd += [str(source), "-o", str(tmp)]
        run(cmd)

    atomic_write_via(output_path, write)


def _compress_gifsicle(
    source: Path, output_path: Path, tools: ToolPaths, *,
    optimize_level: int, lossy: int,
) -> None:
    def write(tmp: Path) -> None:
        cmd = [tools.gifsicle, f"--optimize={optimize_level}"]
        if lossy:
            cmd.append(f"--lossy={lossy}")
        cmd += [str(source), "-o", str(tmp)]
        run(cmd)

    atomic_write_via(output_path, write)


def compress_file(
    input_path: Path,
    codec: str,
    *,
    tools: ToolPaths,
    quality: int | None = None,
    sharpen_amount: float | None = None,
    sharpen_radius: float = 2.0,
    sharpen_sigma: float = 1.0,
    sharpen_threshold: float = 0.0,
    overwrite: bool = False,
    # mozjpeg
    progressive: bool = True,
    subsample: str = "4:2:0",
    # webp
    lossless: bool = False,
    method: int = 6,
    alpha_quality: int = 100,
    # avif
    speed: int = 6,
    # oxipng
    effort: int = 4,
    interlace: bool = False,
    # pngquant
    quality_range: str = "65-90",
    pngquant_speed: int = 4,
    dither: bool = True,
    # gifsicle
    optimize_level: int = 3,
    lossy: int = 0,
) -> CompressOutcome:
    """Re-encodes input_path via codec's specialized encoder (mozjpeg/cwebp/
    avifenc/oxipng/pngquant/gifsicle — the same best-in-class tools
    Squoosh.app itself uses), writing the result beside the source. If
    sharpen_amount is given, an unsharp mask is applied before encoding.
    Raises CompressError for an unrecognized codec/source, and
    MysuiteToolError if an external tool invocation fails."""
    if codec not in CODECS:
        raise CompressError(f"unsupported codec: {codec!r}")

    source_format = detect_source_format(input_path)
    if source_format is None:
        raise CompressError(f"unrecognized source format: {input_path}")

    output_path = output_path_for(input_path, codec)

    if not overwrite and output_path.exists():
        return CompressOutcome(input_path, output_path, "skipped_existing")

    working_path = input_path
    working_format = source_format
    temps: list[Path] = []
    try:
        if sharpen_amount is not None:
            sharpened = _sharpen_to_png(
                working_path, working_format, tools,
                radius=sharpen_radius, sigma=sharpen_sigma,
                amount=sharpen_amount, threshold=sharpen_threshold,
            )
            temps.append(sharpened)
            working_path, working_format = sharpened, "png"

        source, is_temp = _prepare_for_codec(working_path, working_format, codec, tools)
        if is_temp:
            temps.append(source)

        if codec == "mozjpeg":
            _compress_mozjpeg(
                source, output_path, tools,
                quality=quality, progressive=progressive, subsample=subsample,
            )
        elif codec == "webp":
            _compress_webp(
                source, output_path, tools,
                quality=quality, lossless=lossless, method=method,
                alpha_quality=alpha_quality,
            )
        elif codec == "avif":
            _compress_avif(source, output_path, tools, quality=quality, speed=speed)
        elif codec == "oxipng":
            _compress_oxipng(source, output_path, tools, effort=effort, interlace=interlace)
        elif codec == "pngquant":
            _compress_pngquant(
                source, output_path, tools,
                quality_range=quality_range, speed=pngquant_speed, dither=dither,
            )
        else:  # gifsicle
            _compress_gifsicle(source, output_path, tools, optimize_level=optimize_level, lossy=lossy)
    finally:
        for temp in temps:
            temp.unlink(missing_ok=True)

    return CompressOutcome(input_path, output_path, "written")
