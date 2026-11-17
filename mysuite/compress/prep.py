"""What compress hands its encoders: one clean picture, ready for the web.

The encoders (cjpeg, cwebp, avifenc, oxipng, pngquant) know nothing about EXIF rotation, colour profiles or
alpha behind a JPEG, so the picture is prepared here first, with Pillow:

1. opened with :func:`mysuite.utils.imageio.normalize` (EXIF orientation baked into the pixels, 16-bit scaled);
2. converted from its embedded ICC profile (Display P3, Adobe RGB, CMYK …) to sRGB, so the colours stay right;
3. flattened onto a background colour when asked (JPEG has no alpha: transparent areas must not turn black);
4. shrunk so its longest side is at most ``max_size`` (never enlarged).

Only the first frame of an animation is prepared; :attr:`WebImage.notes` says so.
"""
from __future__ import annotations

import io
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageCms, ImageColor, ImageFile, UnidentifiedImageError

from mysuite.compress._parsing import CompressError
from mysuite.utils import imageio


class PrepError(CompressError):
    pass


@dataclass
class WebImage:
    image: Image.Image                       # "RGB" or "RGBA", sRGB pixels, upright
    notes: list[str] = field(default_factory=list)
    frames: int = 1


def srgb_icc() -> bytes:
    """The sRGB profile embedded in web output (lcms' built-in one, about 600 bytes)."""
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


@contextmanager
def _truncated_ok():
    old = ImageFile.LOAD_TRUNCATED_IMAGES
    ImageFile.LOAD_TRUNCATED_IMAGES = True
    try:
        yield
    finally:
        ImageFile.LOAD_TRUNCATED_IMAGES = old


def open_checked(path: Path, notes: list[str]) -> Image.Image:
    """Open and fully load ``path`` (frame 0). A truncated file is loaded as far as it goes, with a note;
    anything Pillow cannot read at all raises :class:`PrepError` with a readable message."""
    try:
        with Image.open(path) as opened:
            frames = getattr(opened, "n_frames", 1)
            try:
                opened.load()
            except OSError as exc:
                if "truncated" not in str(exc).lower():
                    raise
                with _truncated_ok():
                    opened.load()
                notes.append("the file is truncated (incomplete); the missing part is filled with grey")
            image = opened.copy()
            image.info.update(opened.info)
            image.format = opened.format
    except (UnidentifiedImageError, Image.DecompressionBombError, SyntaxError) as exc:
        raise PrepError(f"{path.name} is not an image mysuite can read (or it is damaged)") from exc
    except OSError as exc:
        raise PrepError(f"{path.name} is not an image mysuite can read (or it is damaged): {exc}") from exc
    image.info["_frames"] = frames
    return image


def to_srgb(image: Image.Image, notes: list[str]) -> Image.Image:
    """RGB/RGBA pixels in sRGB. Uses the embedded ICC profile when there is one (P3, Adobe RGB, CMYK …)."""
    has_alpha = image.mode in ("RGBA", "LA", "PA") or (image.mode == "P" and "transparency" in image.info) \
        or (image.mode in ("RGB", "L") and "transparency" in image.info)
    icc = image.info.get("icc_profile")
    alpha = image.convert("RGBA").getchannel("A") if has_alpha else None
    if image.mode in ("P", "PA", "1"):
        image = image.convert("RGBA" if has_alpha else "RGB")
    if image.mode in ("LA", "RGBA", "PA"):
        image = image.convert("RGB" if image.mode == "RGBA" else "L")
    out: Image.Image | None = None
    if icc:
        try:
            source = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            if image.mode not in ("RGB", "CMYK", "L"):
                image = image.convert("RGB")
            out = ImageCms.profileToProfile(image, source, ImageCms.createProfile("sRGB"), outputMode="RGB")
        except (ImageCms.PyCMSError, OSError, ValueError):
            notes.append("the embedded colour profile couldn't be applied; colours may shift")
    if out is None:
        out = image.convert("RGB")
    out.info.pop("icc_profile", None)
    if alpha is not None:
        out = out.convert("RGBA")
        out.putalpha(alpha)
    return out


def flatten_on(image: Image.Image, colour: str) -> Image.Image:
    try:
        rgb = ImageColor.getrgb(colour)[:3]
    except ValueError as exc:
        raise PrepError(f"not a colour: {colour!r} (try white, black or #ffffff)") from exc
    if image.mode != "RGBA":
        return image.convert("RGB")
    flat = Image.new("RGB", image.size, rgb)
    flat.paste(image, mask=image.getchannel("A"))
    return flat


def load_for_web(path: Path, *, flatten: str | None = None, max_size: int | None = None) -> WebImage:
    """Open ``path`` and return it upright, in sRGB, flattened on ``flatten`` (a colour) if given, and with its
    longest side at most ``max_size`` px. Raises :class:`PrepError` when the file is not a readable image."""
    notes: list[str] = []
    image = open_checked(path, notes)
    frames = image.info.get("_frames", 1)
    if frames > 1:
        notes.append(f"{frames} frames in the source; only the first was kept")
    image = imageio.normalize(image)
    image = to_srgb(image, notes)
    if flatten:
        image = flatten_on(image, flatten)
    if max_size and max(image.size) > max_size:
        factor = max_size / max(image.size)
        image = image.resize((max(1, round(image.width * factor)), max(1, round(image.height * factor))),
                             Image.LANCZOS)
    return WebImage(image, notes, frames)
