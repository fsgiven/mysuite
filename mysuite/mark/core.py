"""An invisible ownership mark that survives ordinary handling (JPEG, resizing, cropping, small edits).

How it works (a keyed spread-spectrum watermark, in the spirit of classic correlation watermarks):
* Your secret key (and an optional id) seeds a pseudo-random pattern, band-limited to mid frequencies and tiled across the image.
* The pattern is added, very faintly, to the picture's brightness; flat areas get less, textured areas more.
* To check a picture, mysuite folds it back into one tile (averaging away the picture itself) and correlates it with your
  pattern at several scales. A sharp peak means your pattern is in there; no peak means it is not. Cropping does not
  matter (the peak just moves); resizing is found by trying scales.

What it is NOT: it carries no readable message (it answers "is MY mark in this picture?"), it is not secret from someone who
knows the algorithm AND your key, it is not removed-proof (heavy blur, strong denoising, rotation, re-photographing or a
screenshot at another size can erase it), and it does not stop anyone from using the picture. It is for proving, afterwards,
that a copy came from you.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageOps

from mysuite.utils.subprocess_utils import atomic_write_via

PERIOD = 64
BAND = (0.045, 0.20)             # cycles per pixel: survives a 2x downscale (-> 0.09-0.40) and JPEG
MIN_SIDE = 192
MAX_PIXELS = 60_000_000
DETECT_Z = 7.0
SCALES = [1.0, 0.9, 0.8, 0.75, 0.7, 0.6, 0.5, 0.45, 1.1, 1.25, 1.33, 1.5, 1.75, 2.0, 2.5, 0.4]
STRENGTHS = {"subtle": 2.0, "standard": 3.0, "strong": 4.5}


class MarkError(ValueError):
    pass


def check_key(key: str | None) -> str:
    """The key, or MarkError. Checked once, before any file, so a short key is one error and not one per picture."""
    if not key or len(key) < 4:
        raise MarkError("the key must be at least 4 characters (it is your secret: keep it)")
    return key


def _rng(key: str, ident: str | None) -> np.random.Generator:
    check_key(key)
    seed = int.from_bytes(hashlib.sha256(f"mysuite-mark\0{key}\0{ident or ''}".encode()).digest()[:8], "big")
    return np.random.default_rng(seed)


def make_tile(key: str, ident: str | None = None) -> np.ndarray:
    """The keyed, band-limited, zero-mean, unit-variance pattern of one PERIOD x PERIOD tile."""
    noise = _rng(key, ident).standard_normal((PERIOD, PERIOD))
    f = np.fft.fft2(noise)
    fy = np.fft.fftfreq(PERIOD)[:, None]
    fx = np.fft.fftfreq(PERIOD)[None, :]
    r = np.hypot(fx, fy)
    f *= ((r >= BAND[0]) & (r <= BAND[1])).astype(float)
    tile = np.real(np.fft.ifft2(f))
    tile -= tile.mean()
    return tile / tile.std()


def _blur(a: np.ndarray, r: int) -> np.ndarray:
    k = 2 * r + 1
    pad = np.pad(a, r, mode="reflect")
    c = np.cumsum(np.cumsum(pad, 0), 1)
    c = np.pad(c, ((1, 0), (1, 0)))
    h, w = a.shape
    return (c[k:k + h, k:k + w] - c[:h, k:k + w] - c[k:k + h, :w] + c[:h, :w]) / (k * k)


def texture_mask(luma: np.ndarray) -> np.ndarray:
    """0.35 in flat areas up to 1.5 in busy ones, so the mark hides in texture and stays out of skies and gradients."""
    hp = np.abs(luma - _blur(luma, 2))
    local = _blur(hp, 4)
    ref = float(np.percentile(local, 75)) + 1e-6
    return np.clip(local / ref, 0.35, 1.5)


def _ycbcr(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    return 0.299 * r + 0.587 * g + 0.114 * b, -0.168736 * r - 0.331264 * g + 0.5 * b, 0.5 * r - 0.418688 * g - 0.081312 * b


def _rgb(y: np.ndarray, cb: np.ndarray, cr: np.ndarray) -> np.ndarray:
    return np.stack([y + 1.402 * cr, y - 0.344136 * cb - 0.714136 * cr, y + 1.772 * cb], axis=-1)


@dataclass
class EmbedResult:
    path: Path
    status: str
    psnr_db: float | None = None
    strength: float = 0.0
    notes: list[str] | None = None


FORMATS = {"png": "PNG", "jpeg": "JPEG", "tiff": "TIFF", "webp": "WEBP"}
_BY_SUFFIX = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg", ".tif": "tiff", ".tiff": "tiff", ".webp": "webp"}


def target_format(path: Path, fmt: str | None) -> str:
    """The format to write: the one asked for, else the input's own (JPEG in, JPEG out); PNG for anything else."""
    if fmt:
        fmt = "jpeg" if fmt == "jpg" else fmt
        if fmt not in FORMATS:
            raise MarkError(f"format must be one of {', '.join(FORMATS)}")
        return fmt
    return _BY_SUFFIX.get(Path(path).suffix.lower(), "png")


def output_path_for(path: Path, fmt: str | None = None) -> Path:
    path = Path(path)
    fmt = target_format(path, fmt)
    ext = path.suffix if _BY_SUFFIX.get(path.suffix.lower()) == fmt else {"jpeg": ".jpg", "tiff": ".tif"}.get(fmt, f".{fmt}")
    return path.with_name(f"{path.stem}_marked{ext}")


def _sixteen_bit(im: Image.Image) -> bool:
    if im.mode in ("I;16", "I;16B", "I;16L", "I;16N", "I", "F"):
        return True
    try:
        return any(";16" in str(t[3][0] if isinstance(t[3], tuple) else t[3]) for t in im.tile)
    except (IndexError, TypeError):
        return False


def _cmyk_to_srgb(image: Image.Image, icc: bytes | None) -> tuple[Image.Image, bytes]:
    """CMYK pixels to sRGB, through the embedded CMYK profile when there is one, and the sRGB profile to go with them
    (a CMYK profile on RGB pixels would be wrong)."""
    import io

    from PIL import ImageCms

    srgb = ImageCms.createProfile("sRGB")
    if icc:
        try:
            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            out = ImageCms.profileToProfile(image, src, srgb, outputMode="RGB")
            return out, ImageCms.ImageCmsProfile(srgb).tobytes()
        except (ImageCms.PyCMSError, OSError, ValueError):
            pass
    return image.convert("RGB"), ImageCms.ImageCmsProfile(srgb).tobytes()


def copy_metadata(src: Path, dst: Path, exiftool: str | None, *, icc: bool = True) -> bool:
    """Every tag from the original (EXIF, IPTC, XMP: author, copyright, the no-AI declaration …) onto the marked copy,
    with Orientation reset to 1 because the pixels were turned upright. False when exiftool is not installed."""
    import shutil

    from mysuite.utils.subprocess_utils import run

    tool = shutil.which(exiftool or "exiftool")
    if not tool:
        return False
    run([tool, "-q", "-q", "-m", "-overwrite_original", "-tagsfromfile", str(src), "-all:all", "-unsafe",
         *(["-icc_profile"] if icc else ["--icc_profile:all"]), "-Orientation#=1", str(dst)])
    return True


def embed_file(path: Path, *, key: str, ident: str | None = None, strength: str = "standard", fmt: str | None = None,
               quality: int = 95, overwrite: bool = False, out: Path | None = None, dry_run: bool = False,
               exiftool: str | None = "exiftool") -> EmbedResult:
    """Marks a copy (name_marked.ext, or `out`) in the input's own format unless `fmt` says otherwise, with the
    original's metadata copied over (exiftool). Never changes the original."""
    if strength not in STRENGTHS:
        raise MarkError(f"strength must be one of {', '.join(STRENGTHS)}")
    path = Path(os.path.abspath(path))
    fmt = target_format(path, fmt)
    out = Path(os.path.abspath(out)) if out else output_path_for(path, fmt)
    if out == path:
        raise MarkError("the output would replace the input")
    if out.exists() and not overwrite:
        return EmbedResult(out, "skipped_existing")
    if dry_run:
        return EmbedResult(out, "planned")
    tile = make_tile(key, ident)
    notes: list[str] = []
    converted = False
    try:
        with Image.open(path) as im:
            if im.width * im.height > MAX_PIXELS:
                raise MarkError("image is too large")
            im.seek(0)
            icc = im.info.get("icc_profile")
            if _sixteen_bit(im):
                notes.append("16-bit source: the marked copy is 8-bit per channel")
            from mysuite.utils.imageio import normalize

            upright = normalize(im)                            # upright, 16-bit scaled (not clipped) to 8-bit
            if upright.mode == "CMYK":
                upright, icc = _cmyk_to_srgb(upright, icc)
                converted = True
                notes.append("CMYK source: the marked copy is RGB (sRGB); keep the CMYK original for print")
            image = upright.convert("RGBA")
    except MarkError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise MarkError(f"can't read {path.name}: {exc}") from exc
    if min(image.size) < MIN_SIDE:
        raise MarkError(f"the image is too small to carry a reliable mark (at least {MIN_SIDE} px on each side)")
    rgba = np.asarray(image, dtype=np.float64)
    rgb, alpha = rgba[..., :3], rgba[..., 3]
    h, w, _ = rgb.shape
    y, cb, cr = _ycbcr(rgb)
    pattern = np.tile(tile, (h // PERIOD + 1, w // PERIOD + 1))[:h, :w]
    amplitude = STRENGTHS[strength]
    marked_y = y + amplitude * texture_mask(y) * pattern
    out_rgb = np.clip(np.round(_rgb(marked_y, cb, cr)), 0, 255)
    mse = float(np.mean((out_rgb - rgb) ** 2))
    result = Image.fromarray(out_rgb.astype(np.uint8), "RGB")
    if alpha.min() < 255:
        if fmt == "jpeg":
            notes.append("JPEG has no transparency: transparent areas are now black-backed; use PNG to keep them")
        else:
            result = result.convert("RGBA")
            result.putalpha(Image.fromarray(alpha.astype(np.uint8), "L"))
    if fmt == "jpeg":
        result = result.convert("RGB")
        if _BY_SUFFIX.get(path.suffix.lower()) != "jpeg":
            notes.append("saved as JPEG: the mark is built to survive this, but PNG keeps it strongest")
    kwargs: dict[str, Any] = {"icc_profile": icc} if icc else {}
    if fmt == "jpeg":
        kwargs.update(quality=quality, subsampling=0)
    elif fmt == "webp":
        kwargs.update(quality=max(quality, 95), method=6)
    elif fmt == "tiff":
        kwargs.update(compression="tiff_lzw")

    def write(tmp: Path) -> None:
        result.save(tmp, format=FORMATS[fmt], **kwargs)
        if exiftool is not None and not copy_metadata(path, tmp, exiftool, icc=not converted):
            notes.append("metadata not copied: exiftool is not installed (the colour profile is kept)")

    atomic_write_via(out, write, preserve_extension=True)
    return EmbedResult(out, "written", None if mse == 0 else round(10 * float(np.log10(255 ** 2 / mse)), 2), amplitude, notes)


# ----------------------------------------------------------------------------------------- detect
@dataclass
class Detection:
    detected: bool
    z: float
    scale: float
    shift: tuple[int, int]
    scales_tried: int
    width: int
    height: int
    mirrored: bool = False
    angle: float = 0.0


def _fold(img: np.ndarray) -> np.ndarray | None:
    h, w = img.shape
    th, tw = h // PERIOD, w // PERIOD
    if th < 2 or tw < 2:
        return None
    return img[: th * PERIOD, : tw * PERIOD].reshape(th, PERIOD, tw, PERIOD).mean(axis=(0, 2))


def _peak_z(folded: np.ndarray, tile_f: np.ndarray) -> tuple[float, tuple[int, int]]:
    c = np.real(np.fft.ifft2(np.fft.fft2(folded - folded.mean()) * tile_f))
    peak_idx = np.unravel_index(int(np.argmax(c)), c.shape)
    peak = c[peak_idx]
    mask = np.ones_like(c, dtype=bool)
    for dy in range(-2, 3):
        for dx in range(-2, 3):
            mask[(peak_idx[0] + dy) % PERIOD, (peak_idx[1] + dx) % PERIOD] = False
    rest = c[mask]
    sd = rest.std()
    return float((peak - rest.mean()) / sd) if sd > 0 else 0.0, (int(peak_idx[0]), int(peak_idx[1]))


def detect_file(path: Path, *, key: str, ident: str | None = None, max_side: int = 1800, deep: bool = False) -> Detection:
    tile = make_tile(key, ident)
    tile_f = np.conj(np.fft.fft2(tile))
    try:
        with Image.open(path) as im:
            if im.width * im.height > MAX_PIXELS:
                raise MarkError("image is too large")
            im.seek(0)
            from mysuite.utils.imageio import normalize

            gray = normalize(im).convert("RGBA")
    except MarkError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise MarkError(f"can't read {Path(path).name}: {exc}") from exc
    width, height = gray.size
    if min(width, height) < MIN_SIDE:
        raise MarkError(f"the picture is too small to check (at least {MIN_SIDE} px on each side)")
    white = Image.new("RGBA", gray.size, "white")
    luma_img = Image.alpha_composite(white, gray).convert("L")
    if max(luma_img.size) > max_side:
        k = max_side / max(luma_img.size)
        luma_img = luma_img.resize((round(luma_img.width * k), round(luma_img.height * k)), Image.Resampling.LANCZOS)
    base_scale = width / luma_img.width
    best = Detection(False, 0.0, 1.0, (0, 0), 0, width, height)

    def consider(img: Image.Image, scale: float, mirrored: bool, angle: float) -> None:
        nonlocal best
        w2, h2 = max(1, round(img.width * base_scale / scale)), max(1, round(img.height * base_scale / scale))
        if min(w2, h2) < MIN_SIDE // 2:
            return
        arr = np.asarray(img.resize((w2, h2), Image.Resampling.LANCZOS), dtype=np.float64)
        folded = _fold(arr - _blur(arr, 3))
        if folded is None:
            return
        best.scales_tried += 1
        zz, shift = _peak_z(folded, tile_f)
        if zz > best.z:
            best = Detection(False, zz, scale, shift, best.scales_tried, width, height, mirrored, angle)

    # the suspect may have been scaled since marking: undo each candidate scale so the pattern's period is PERIOD again
    for mirrored in (False, True):
        img = luma_img.transpose(Image.FLIP_LEFT_RIGHT) if mirrored else luma_img
        for s in SCALES:
            consider(img, s, mirrored, 0.0)
    if deep and best.z < DETECT_Z:
        # a slightly rotated copy: the pattern is only coherent if the angle is undone to ~0.1 degree
        top = sorted({round(best.scale, 2), 1.0, 0.5, 0.75})
        for mirrored in (False, True):
            img0 = luma_img.transpose(Image.FLIP_LEFT_RIGHT) if mirrored else luma_img
            for step in range(-30, 31):
                angle = step * 0.1
                if abs(angle) < 0.05:
                    continue
                img = img0.rotate(-angle, resample=Image.Resampling.BICUBIC, fillcolor=128)
                for s in top:
                    consider(img, s, mirrored, angle)
    best.z = round(best.z, 2)
    best.detected = best.z >= DETECT_Z
    return best
