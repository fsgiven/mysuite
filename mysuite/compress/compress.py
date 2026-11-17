from __future__ import annotations

import tempfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from mysuite.compress._parsing import CODECS, CompressError, output_path_for
from mysuite.compress.prep import WebImage, load_for_web, srgb_icc
from mysuite.config import ToolPaths
from mysuite.convert._parsing import detect_source_format
from mysuite.utils.subprocess_utils import atomic_write_via, run

__all__ = ["CompressError", "CompressOutcome", "compress_file"]

_DEFAULT_DPI = 300.0
_VECTOR = ("svg", "pdf", "eps")

# The file each codec's encoder reads. Every source is prepared with Pillow first (mysuite.compress.prep:
# upright, sRGB, flattened for JPEG) and written in this format; mozjpeg's cjpeg only reads PPM.
_PREPARE_EXTENSION: dict[str, str] = {
    "mozjpeg": ".ppm",
    "webp": ".png",
    "avif": ".png",
    "oxipng": ".png",
    "pngquant": ".png",
    "gifsicle": ".gif",
}
# Codecs whose quality --max-kb can search (the others are lossless or palette-based).
MAX_KB_CODECS = ("mozjpeg", "webp", "avif")
_MIN_QUALITY = 20      # below this a photo falls apart; --max-size is the better way to get smaller
_TOP_QUALITY = 95

# Codecs without an alpha channel: transparency is flattened (white unless --background says otherwise).
_NO_ALPHA = {"mozjpeg"}


class _NotSmaller(Exception):
    def __init__(self, size: int) -> None:
        self.size = size


@dataclass
class CompressOutcome:
    input_path: Path
    output_path: Path
    status: str  # "written" | "skipped_existing" | "not_smaller" (nothing written: it would be bigger)
    bytes_before: int = 0
    bytes_after: int = 0
    size: tuple[int, int] = (0, 0)
    quality: int | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def ratio(self) -> float | None:
        return round(self.bytes_after / self.bytes_before, 3) if self.bytes_before and self.bytes_after else None


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
            tools.gs, "-dNOPAUSE", "-dBATCH", "-dSAFER", "-sDEVICE=png16m", "-dFirstPage=1", "-dLastPage=1",
            f"-r{_DEFAULT_DPI}", f"-sOutputFile={tmp_png}", str(input_path),
        ])
    return tmp_png


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return len(data).to_bytes(4, "big") + kind + data + zlib.crc32(kind + data).to_bytes(4, "big")


def _png_add_chunks(path: Path, chunks: list[bytes]) -> None:
    """Insert ready-made chunks right after IHDR (pngquant drops iCCP/eXIf; iCCP must come before PLTE/IDAT)."""
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n" or not chunks:
        return
    ihdr_end = 8 + 8 + int.from_bytes(data[8:12], "big") + 4
    present = set()
    pos = ihdr_end
    while pos + 8 <= len(data):
        length = int.from_bytes(data[pos:pos + 4], "big")
        present.add(data[pos + 4:pos + 8])
        pos += 12 + length
    new = [c for c in chunks if c[4:8] not in present]
    path.write_bytes(data[:ihdr_end] + b"".join(new) + data[ihdr_end:])


def _iccp_chunk(icc: bytes) -> bytes:
    return _png_chunk(b"iCCP", b"sRGB\x00\x00" + zlib.compress(icc))


def _exif_chunk(exif: bytes) -> bytes:
    return _png_chunk(b"eXIf", exif[6:] if exif.startswith(b"Exif\x00\x00") else exif)


def _jpeg_add_exif(path: Path, exif: bytes) -> None:
    """Insert an APP1 Exif segment after SOI (and after the JFIF APP0, when cjpeg wrote one)."""
    data = path.read_bytes()
    if data[:2] != b"\xff\xd8":
        return
    payload = exif if exif.startswith(b"Exif\x00\x00") else b"Exif\x00\x00" + exif
    segment = b"\xff\xe1" + (len(payload) + 2).to_bytes(2, "big") + payload
    pos = 2
    if data[2:4] == b"\xff\xe0":
        pos = 4 + int.from_bytes(data[4:6], "big")
    path.write_bytes(data[:pos] + segment + data[pos:])


def _is_animated_png(path: Path) -> bool:
    try:
        with Image.open(path) as im:
            return getattr(im, "n_frames", 1) > 1
    except Exception:  # noqa: BLE001 - not readable here: the prep step reports it properly
        return False


def _native(input_path: Path, source_format: str, codec: str) -> bool:
    """Animations a codec can keep go straight to its encoder (prep keeps only the first frame)."""
    if codec == "gifsicle" and source_format == "gif":
        return True
    return codec == "oxipng" and source_format == "png" and _is_animated_png(input_path)


def _write_prepared(web: WebImage, input_path: Path, codec: str, tools: ToolPaths, sharpen: dict | None) -> Path:
    ext = _PREPARE_EXTENSION[codec]
    tmp = _tmp_sibling(input_path, ext)
    image = web.image
    if ext == ".ppm":
        image.convert("RGB").save(tmp, format="PPM")
    elif ext == ".gif":
        image.save(tmp, format="GIF")
    else:
        extra = {"exif": web.exif} if web.exif else {}
        image.save(tmp, format="PNG", compress_level=1, icc_profile=srgb_icc(), **extra)
    if sharpen is None:
        return tmp
    sharp = _tmp_sibling(input_path, ".sharpened" + ext)
    try:
        run([tools.magick, str(tmp),
             "-unsharp", f"{sharpen['radius']}x{sharpen['sigma']}+{sharpen['amount']}+{sharpen['threshold']}",
             str(sharp)])
    finally:
        tmp.unlink(missing_ok=True)
    return sharp


def _encode(codec: str, source: Path, dest: Path, tools: ToolPaths, icc_file: Path | None, s: dict,
            exif: bytes | None = None, exif_file: Path | None = None) -> None:
    quality = s["quality"]
    if codec == "mozjpeg":
        cmd = [tools.cjpeg, "-sample", "1x1" if s["subsample"] == "4:4:4" else "2x2", "-outfile", str(dest)]
        if quality is not None:
            cmd += ["-quality", str(quality)]
        cmd += ["-progressive"] if s["progressive"] else ["-baseline"]
        if icc_file is not None:
            cmd += ["-icc", str(icc_file)]
        run([*cmd, str(source)])
        if exif:
            _jpeg_add_exif(dest, exif)
    elif codec == "webp":
        cmd = [tools.cwebp, "-m", str(s["method"]), "-alpha_q", str(s["alpha_quality"]), "-metadata", "icc,exif"]
        if s["lossless"]:
            cmd.append("-lossless")
        cmd += ["-q", str(quality if quality is not None else 75)]
        run([*cmd, str(source), "-o", str(dest)])
    elif codec == "avif":
        cmd = [tools.avifenc, "-s", str(s["speed"])]
        if quality is not None:
            cmd += ["-q", str(quality)]
        if icc_file is not None:
            cmd += ["--icc", str(icc_file)]
        cmd += ["--exif", str(exif_file)] if exif_file is not None else ["--ignore-exif", "--ignore-xmp"]
        run([*cmd, str(source), str(dest)])
    elif codec == "oxipng":
        # A prepared PNG holds only iCCP (+ eXIf with the copyright): keep them. A native APNG is stripped.
        strip = ["--strip", "safe"] if icc_file is None else []
        cmd = [tools.oxipng, "-o", str(s["effort"]), *strip, "-i", "on" if s["interlace"] else "off"]
        run([*cmd, str(source), "--out", str(dest)])
    elif codec == "pngquant":
        cmd = [tools.pngquant, f"--quality={s['quality_range']}", "--speed", str(s["pngquant_speed"]), "--force"]
        if not s["dither"]:
            cmd.append("--nofs")
        run([*cmd, str(source), "-o", str(dest)])
        if icc_file is not None:
            _png_add_chunks(dest, [_iccp_chunk(icc_file.read_bytes()), *([_exif_chunk(exif)] if exif else [])])
    else:  # gifsicle
        cmd = [tools.gifsicle, f"--optimize={s['optimize_level']}"]
        if s["lossy"]:
            cmd.append(f"--lossy={s['lossy']}")
        run([*cmd, str(source), "-o", str(dest)])


def _search_quality(encode, input_path: Path, limit: int, start: int | None, temps: list[Path]) -> int:
    """The highest quality whose file is at most ``limit`` bytes (binary search, about 6 encodes)."""
    probe = _tmp_sibling(input_path, ".probe")
    temps.append(probe)
    top = start if start is not None else _TOP_QUALITY
    if encode(probe, top) <= limit:
        return top
    low_size = encode(probe, _MIN_QUALITY)
    if low_size > limit:
        raise CompressError(
            f"{input_path.name} can't get under {limit // 1000} KB even at quality {_MIN_QUALITY} "
            f"({low_size // 1000} KB): add --max-size (fewer pixels) or allow a bigger file"
        )
    lo, hi = _MIN_QUALITY, top - 1          # lo always fits; find the highest that does
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if encode(probe, mid) <= limit:
            lo = mid
        else:
            hi = mid - 1
    return lo


def compress_file(
    input_path: Path,
    codec: str,
    *,
    tools: ToolPaths,
    output_path: Path | None = None,
    quality: int | None = None,
    background: str | None = None,
    allow_larger: bool = False,
    max_kb: int | None = None,
    max_size: int | None = None,
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
    """Re-encodes input_path with codec's specialised encoder (mozjpeg/cwebp/avifenc/oxipng/pngquant/gifsicle,
    the tools Squoosh.app uses), beside the source unless ``output_path`` is given.

    The picture is prepared first (see mysuite.compress.prep): upright, converted to sRGB (and tagged sRGB),
    transparency flattened on ``background`` (white by default) for JPEG. A raster result that would be larger
    than the source is not written (status ``not_smaller``) unless ``allow_larger``. Raises CompressError for an
    unknown codec or an unreadable source, MysuiteToolError if an encoder fails."""
    if codec not in CODECS:
        raise CompressError(f"unsupported codec: {codec!r}")
    if max_kb is not None and codec not in MAX_KB_CODECS:
        raise CompressError(f"--max-kb needs a codec with a quality setting ({', '.join(MAX_KB_CODECS)}), not {codec}")

    source_format = detect_source_format(input_path)
    if source_format is None:
        raise CompressError(f"unrecognized source format: {input_path}")

    output_path = output_path or output_path_for(input_path, codec)
    if not overwrite and output_path.exists():
        return CompressOutcome(input_path, output_path, "skipped_existing")

    bytes_before = input_path.stat().st_size
    settings = {
        "quality": quality, "progressive": progressive, "subsample": subsample, "lossless": lossless,
        "method": method, "alpha_quality": alpha_quality, "speed": speed, "effort": effort,
        "interlace": interlace, "quality_range": quality_range, "pngquant_speed": pngquant_speed,
        "dither": dither, "optimize_level": optimize_level, "lossy": lossy,
    }
    sharpen = None if sharpen_amount is None else {
        "amount": sharpen_amount, "radius": sharpen_radius, "sigma": sharpen_sigma, "threshold": sharpen_threshold,
    }

    temps: list[Path] = []
    notes: list[str] = []
    size: tuple[int, int] = (0, 0)
    exif: bytes | None = None
    exif_file: Path | None = None
    try:
        if _native(input_path, source_format, codec) and sharpen is None and max_size is None:
            source, icc_file = input_path, None
            with Image.open(input_path) as im:
                size = im.size
        else:
            raster = input_path
            if source_format in _VECTOR:
                raster = _rasterize_to_png(input_path, source_format, tools)
                temps.append(raster)
            flatten = background or ("white" if codec in _NO_ALPHA else None)
            web = load_for_web(raster, flatten=flatten, max_size=max_size)
            hint = " (--codec gifsicle keeps a GIF animation)" if source_format == "gif" else ""
            notes += [n + hint if n.startswith(f"{web.frames} frames") else n for n in web.notes]
            size = web.image.size
            source = _write_prepared(web, input_path, codec, tools, sharpen)
            temps.append(source)
            icc_file = _tmp_sibling(input_path, ".icc")
            icc_file.write_bytes(srgb_icc())
            temps.append(icc_file)
            exif = web.exif
            if exif and codec == "avif":
                exif_file = _tmp_sibling(input_path, ".exif")
                exif_file.write_bytes(exif)
                temps.append(exif_file)

        def encode(dest: Path, q: int | None) -> int:
            _encode(codec, source, dest, tools, icc_file, {**settings, "quality": q}, exif, exif_file)
            return dest.stat().st_size

        if max_kb is not None:
            quality = _search_quality(encode, input_path, max_kb * 1000, quality, temps)

        check_size = not allow_larger and source_format not in _VECTOR

        def write(tmp: Path) -> None:
            produced = encode(tmp, quality)
            if check_size and produced >= bytes_before:
                raise _NotSmaller(produced)

        try:
            atomic_write_via(output_path, write, preserve_extension=True)
        except _NotSmaller as exc:
            return CompressOutcome(input_path, output_path, "not_smaller", bytes_before, exc.size, size,
                                   quality, notes)
    finally:
        for temp in temps:
            temp.unlink(missing_ok=True)

    return CompressOutcome(input_path, output_path, "written", bytes_before, output_path.stat().st_size, size,
                           quality, notes)
