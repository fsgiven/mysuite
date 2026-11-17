"""One way to read pixels: upright, 8 bits per channel, ICC profile kept.

Pillow opens a 16-bit greyscale PNG/TIFF as mode ``I;16`` (or ``I``/``F``), and ``convert("RGB")`` on those
*clips* every value above 255, so a normal photo comes out solid white. Use :func:`to_8bit` (or
:func:`normalize` / :func:`open_image`) before any ``convert`` so the tones are scaled instead.
16-bit RGB(A) PNGs are already reduced to 8 bits correctly by Pillow and pass through unchanged.

House rule for **web output** (compress / "Save for web", see mysuite.compress.prep): the file is upright, its pixels
are sRGB and the sRGB profile is embedded; GPS, serial numbers, camera and dates are stripped; the copyright and author
are kept. Other tools that write web images should follow the same rule.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

_SIXTEEN = ("I;16", "I;16L", "I;16B", "I;16N")


def to_8bit(image: Image.Image) -> Image.Image:
    """Return ``image`` as 8-bit ``L`` if it is a 16/32-bit integer or float greyscale image, scaled (not clipped).

    Other modes are returned as they are (the same object). ``info`` (ICC profile, dpi, ...) is carried over.
    """
    mode = image.mode
    if mode not in (*_SIXTEEN, "I", "F"):
        return image
    arr = np.asarray(image, dtype=np.float64)
    top = arr.max() if arr.size else 0
    if mode in _SIXTEEN:
        factor = 1 / 257.0                                       # 65535 -> 255
    elif mode == "I":
        # 32-bit integer images nearly always hold 16-bit data (that's how PNG/TIFF readers deliver it);
        # only data already in 0..255 stays as it is.
        factor = 1 / 257.0 if top > 255 else 1.0
    else:                                                        # "F": 0..1 floats, or 0..255
        factor = 255.0 if top <= 1.0 else 1.0
    out = Image.fromarray(np.clip(np.rint(arr * factor), 0, 255).astype(np.uint8))
    out.info.update(image.info)
    if isinstance(out.info.get("transparency"), (int, float)):    # a 16-bit transparent grey value, scaled the same way
        out.info["transparency"] = int(np.clip(round(out.info["transparency"] * factor), 0, 255))
    return out


def normalize(image: Image.Image, *, orient: bool = True) -> Image.Image:
    """EXIF orientation applied (pixels turned upright), then :func:`to_8bit`. ICC and other ``info`` are kept."""
    if orient:
        icc = image.info.get("icc_profile")
        image = ImageOps.exif_transpose(image)
        if icc and "icc_profile" not in image.info:
            image.info["icc_profile"] = icc
    return to_8bit(image)


def open_image(path: Path | str, *, orient: bool = True, frame: int = 0) -> Image.Image:
    """Open ``path``, pick ``frame``, and return a loaded, upright, 8-bit image (the file is closed again)."""
    with Image.open(path) as opened:
        opened.seek(frame)
        opened.load()
        out = normalize(opened, orient=orient)
        if out is opened:
            out = opened.copy()
            out.info.update(opened.info)
        out.load()
        return out
