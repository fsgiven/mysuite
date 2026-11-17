"""What compress hands its encoders: one clean picture, ready for the web.

The encoders (cjpeg, cwebp, avifenc, oxipng, pngquant) know nothing about EXIF rotation, colour profiles or
alpha behind a JPEG, so the picture is prepared here first, with Pillow:

1. opened with :func:`mysuite.utils.imageio.normalize` (EXIF orientation baked into the pixels, 16-bit scaled);
2. converted from its embedded ICC profile (Display P3, Adobe RGB, CMYK …) to sRGB, so the colours stay right;
3. flattened onto a background colour when asked (JPEG has no alpha: transparent areas must not turn black);
4. shrunk so its longest side is at most ``max_size`` (never enlarged).

Web output keeps only sRGB and the owner (EXIF Copyright/Artist, found in EXIF, XMP or IPTC): GPS, serial
numbers, camera and dates never reach the encoder, because it gets a freshly written file.

Only the first frame of an animation is prepared; :attr:`WebImage.notes` says so.
"""
from __future__ import annotations

import html
import io
import re
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
    exif: bytes | None = None                # only the copyright and author, for the output (nothing else)
    had_profile: bool = False                # the source carried an ICC profile (its pixels were converted)


# The web rule: the output carries the sRGB profile and who owns the picture, nothing else - no GPS, no
# serial numbers, no camera, no dates.
_RIGHTS_TAGS = {0x8298: "copyright", 0x013B: "artist"}
_XMP_RIGHTS = re.compile(rb"<dc:rights>.*?<rdf:li[^>]*>(.*?)</rdf:li>", re.S)
_XMP_CREATOR = re.compile(rb"<dc:creator>.*?<rdf:li[^>]*>(.*?)</rdf:li>", re.S)


def _exif_text(value) -> str | None:
    """EXIF "ASCII" is UTF-8 in practice (Lightroom, exiftool); Pillow hands it back decoded as Latin-1."""
    if isinstance(value, str):
        try:
            return value.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return value
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return None


def _rights(opened: Image.Image) -> dict[str, str]:
    """Copyright and author from EXIF, then XMP (dc:rights / dc:creator), then IPTC (JPEG)."""
    found: dict[str, str] = {}
    try:
        exif = opened.getexif()
        for tag, key in _RIGHTS_TAGS.items():
            value = _exif_text(exif.get(tag))
            if isinstance(value, str) and value.strip("\x00 "):
                found[key] = value.strip("\x00 ")
    except Exception:  # noqa: BLE001 - broken EXIF: there is just nothing to keep
        pass
    xmp = opened.info.get("xmp") or opened.info.get("XML:com.adobe.xmp")
    if isinstance(xmp, str):
        xmp = xmp.encode("utf-8")
    if isinstance(xmp, bytes):
        for key, pattern in (("copyright", _XMP_RIGHTS), ("artist", _XMP_CREATOR)):
            match = pattern.search(xmp)
            if key not in found and match and match.group(1).strip():
                found[key] = html.unescape(match.group(1).decode("utf-8", "replace").strip())
    if opened.format == "JPEG":
        try:
            from PIL import IptcImagePlugin

            iptc = IptcImagePlugin.getiptcinfo(opened) or {}
            for key, record in (("copyright", (2, 116)), ("artist", (2, 80))):
                value = iptc.get(record)
                if isinstance(value, list):
                    value = value[0] if value else None
                if key not in found and isinstance(value, bytes) and value.strip():
                    found[key] = value.decode("utf-8", "replace").strip()
        except Exception:  # noqa: BLE001
            pass
    return found


def rights_exif(rights: dict[str, str]) -> bytes | None:
    """An EXIF block holding only Copyright and Artist (b"Exif\\0\\0" + TIFF), or None when there are none."""
    if not rights:
        return None
    exif = Image.Exif()
    for tag, key in _RIGHTS_TAGS.items():
        if rights.get(key):
            exif[tag] = rights[key].encode("utf-8")      # bytes: Pillow would turn a str's "©" into "?"
    return exif.tobytes()


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
            image.info["_rights"] = _rights(opened)
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
    exif = rights_exif(image.info.get("_rights") or {})
    had_profile = bool(image.info.get("icc_profile"))
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
    return WebImage(image, notes, frames, exif, had_profile)
