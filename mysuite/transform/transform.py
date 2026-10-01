"""Small, predictable image edits: auto-orient, trim, crop, rotate, flip, resize, pad, round, background.

Pure Pillow (vector sources are rasterised first). Operations always run in this fixed order, whatever the
order of the flags, so an agent can reason about the result:

    auto-orient -> trim -> crop -> rotate -> flip -> resize -> pad -> round -> background

The output carries no metadata (EXIF/GPS dropped, like `enhance`); the colour profile is kept.
"""
from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageOps

from mysuite.color.parse import parse_color
from mysuite.config import ToolPaths
from mysuite.convert._parsing import RASTER_FORMATS, detect_source_format
from mysuite.enhance.enhance import _bit_depth
from mysuite.utils.subprocess_utils import atomic_write_via, run

FORMATS = {"png": ".png", "jpeg": ".jpg", "webp": ".webp", "tiff": ".tiff"}
GRAVITY = {
    "center": (0.5, 0.5), "top": (0.5, 0.0), "bottom": (0.5, 1.0), "left": (0.0, 0.5), "right": (1.0, 0.5),
    "top-left": (0.0, 0.0), "top-right": (1.0, 0.0), "bottom-left": (0.0, 1.0), "bottom-right": (1.0, 1.0),
}
MAX_PIXELS = 100_000_000


class TransformError(ValueError):
    pass


@dataclass
class Ops:
    auto_orient: bool = True
    trim: bool = False
    trim_fuzz: float = 0.0               # percent of 255 a pixel may differ from the border colour
    crop: str | None = None              # "WxH+X+Y" pixels
    crop_aspect: str | None = None       # "16:9"
    gravity: str = "center"
    rotate: float | None = None          # degrees clockwise
    flip: str | None = None              # horizontal | vertical | both
    resize: str | None = None            # "512" width, "x512" height, "512x512" box, "50%"
    resize_mode: str = "fit"             # fit (inside the box) | fill (cover the box, crop) | exact
    shrink_only: bool = False
    pad: str | None = None               # "1:1" aspect or "WxH" canvas
    pad_color: str | None = None         # default transparent (white for JPEG)
    round: str | None = None             # corner radius "24" px or "50%"
    background: str | None = None        # flatten onto this colour
    format: str | None = None
    quality: int | None = None

    def validate(self) -> None:
        if self.crop and self.crop_aspect:
            raise TransformError("use either crop (a box) or crop_aspect, not both")
        for name in ("pad_color", "background"):
            value = getattr(self, name)
            if value and parse_color(value) is None:
                raise TransformError(f"{name}: not a colour: {value!r}")
        if self.gravity not in GRAVITY:
            raise TransformError(f"unknown gravity {self.gravity!r} - expected one of {', '.join(GRAVITY)}")
        if self.flip not in (None, "horizontal", "vertical", "both"):
            raise TransformError("flip must be horizontal, vertical or both")
        if self.resize_mode not in ("fit", "fill", "exact"):
            raise TransformError("resize_mode must be fit, fill or exact")
        if self.format not in (None, *FORMATS):
            raise TransformError(f"format must be one of {', '.join(FORMATS)}")
        if self.quality is not None and not 1 <= self.quality <= 100:
            raise TransformError("quality must be 1-100")
        if not 0 <= self.trim_fuzz <= 100:
            raise TransformError("trim_fuzz must be 0-100")
        # parse-check the specs now so a bad value fails before any file is touched
        if self.crop:
            parse_crop(self.crop)
        if self.crop_aspect:
            parse_aspect(self.crop_aspect)
        if self.resize:
            parse_resize(self.resize)
        if self.pad:
            parse_pad(self.pad)
        if self.round:
            parse_radius(self.round)

    def active(self) -> list[str]:
        names = ["trim"] if self.trim else []
        for name in ("crop", "crop_aspect", "rotate", "flip", "resize", "pad", "round", "background"):
            if getattr(self, name) is not None:
                names.append(name)
        return names


# --------------------------------------------------------------------------- parsing
_CROP = re.compile(r"^(\d+)x(\d+)\+(\d+)\+(\d+)$")
_ASPECT = re.compile(r"^(\d+(?:\.\d+)?):(\d+(?:\.\d+)?)$")
_RESIZE = re.compile(r"^(?:(\d+)?x(\d+)?|(\d+)|(\d+(?:\.\d+)?)%)$")


def parse_crop(text: str) -> tuple[int, int, int, int]:
    m = _CROP.match(text.strip())
    if not m or int(m.group(1)) < 1 or int(m.group(2)) < 1:
        raise TransformError(f"crop must look like WIDTHxHEIGHT+X+Y, e.g. 800x600+100+50, got {text!r}")
    w, h, x, y = (int(v) for v in m.groups())
    return w, h, x, y


def parse_aspect(text: str) -> float:
    m = _ASPECT.match(text.strip())
    if not m or float(m.group(2)) == 0 or float(m.group(1)) == 0:
        raise TransformError(f"aspect must look like 16:9 or 1:1, got {text!r}")
    return float(m.group(1)) / float(m.group(2))


def parse_resize(text: str) -> tuple[str, Any]:
    """-> ("scale", factor) | ("box", (w|None, h|None))"""
    t = text.strip().lower()
    m = _RESIZE.match(t)
    if not m or t in ("x", ""):
        raise TransformError(f"resize must be WIDTH, xHEIGHT, WIDTHxHEIGHT or N%, got {text!r}")
    if m.group(4):
        factor = float(m.group(4)) / 100
        if factor <= 0:
            raise TransformError("resize percentage must be above 0")
        return "scale", factor
    if m.group(3):
        return "box", (int(m.group(3)), None)
    w = int(m.group(1)) if m.group(1) else None
    h = int(m.group(2)) if m.group(2) else None
    if (w is not None and w < 1) or (h is not None and h < 1):
        raise TransformError("resize dimensions must be at least 1")
    return "box", (w, h)


def parse_pad(text: str) -> tuple[str, Any]:
    t = text.strip().lower()
    if _ASPECT.match(t):
        return "aspect", parse_aspect(t)
    m = re.match(r"^(\d+)x(\d+)$", t)
    if m and int(m.group(1)) > 0 and int(m.group(2)) > 0:
        return "size", (int(m.group(1)), int(m.group(2)))
    raise TransformError(f"pad must be an aspect like 1:1 or a size like 1200x630, got {text!r}")


def parse_radius(text: str) -> tuple[str, float]:
    t = text.strip().lower()
    try:
        if t.endswith("%"):
            v = float(t[:-1])
            if not 0 < v <= 50:
                raise TransformError("round percentage must be above 0 and at most 50 (50% = circle)")
            return "pct", v / 100
        v = float(t)
    except ValueError as exc:
        raise TransformError(f"round must be a pixel radius or a percentage like 50%, got {text!r}") from exc
    if v <= 0:
        raise TransformError("round radius must be above 0")
    return "px", v


# --------------------------------------------------------------------------- operations
def _rgba(color: str | None, default: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    if not color:
        return default
    c = parse_color(color)
    assert c is not None
    return c.r, c.g, c.b, round((c.alpha if c.alpha is not None else 1.0) * 255)


def trim(im: Image.Image, fuzz_pct: float) -> Image.Image:
    """Cuts away a uniform border: transparent pixels, else the colour of the top-left corner (within fuzz)."""
    rgba = im.convert("RGBA")
    alpha = rgba.getchannel("A")
    if alpha.getextrema()[0] < 255:
        mask = alpha.point(lambda a: 255 if a > 0 else 0)
    else:
        ref = Image.new("RGBA", rgba.size, rgba.getpixel((0, 0)))
        diff = ImageChops.difference(rgba, ref).convert("L")
        limit = round(fuzz_pct / 100 * 255)
        mask = diff.point(lambda d: 255 if d > limit else 0)
    box = mask.getbbox()
    if box is None:
        return im          # nothing but border: leave it, never return an empty image
    return im.crop(box)


def crop_box(im: Image.Image, spec: str) -> Image.Image:
    w, h, x, y = parse_crop(spec)
    if x + w > im.width or y + h > im.height:
        raise TransformError(f"crop {spec} is outside the {im.width}x{im.height} image")
    return im.crop((x, y, x + w, y + h))


def crop_aspect(im: Image.Image, spec: str, gravity: str) -> Image.Image:
    target = parse_aspect(spec)
    current = im.width / im.height
    gx, gy = GRAVITY[gravity]
    if abs(current - target) < 1e-9:
        return im
    if current > target:                      # too wide: cut the sides
        new_w = max(1, round(im.height * target))
        x = round((im.width - new_w) * gx)
        return im.crop((x, 0, x + new_w, im.height))
    new_h = max(1, round(im.width / target))  # too tall: cut top/bottom
    y = round((im.height - new_h) * gy)
    return im.crop((0, y, im.width, y + new_h))


def resize(im: Image.Image, spec: str, mode: str, shrink_only: bool, gravity: str) -> Image.Image:
    kind, value = parse_resize(spec)
    w, h = im.size
    if kind == "scale":
        tw, th = max(1, round(w * value)), max(1, round(h * value))
        box_w, box_h = tw, th
        mode = "exact"
    else:
        bw, bh = value
        if bw and bh:
            box_w, box_h = bw, bh
        elif bw:
            box_w, box_h = bw, max(1, round(h * bw / w))
        else:
            box_w, box_h = max(1, round(w * bh / h)), bh
        tw, th = box_w, box_h
    if mode == "fit":
        scale = min(box_w / w, box_h / h)
        tw, th = max(1, round(w * scale)), max(1, round(h * scale))
    if mode == "fill":
        scale = max(box_w / w, box_h / h)
        sw, sh = max(1, round(w * scale)), max(1, round(h * scale))
        if shrink_only and (sw > w or sh > h):
            return im
        scaled = im.resize((sw, sh), Image.Resampling.LANCZOS)
        gx, gy = GRAVITY[gravity]
        x, y = round((sw - box_w) * gx), round((sh - box_h) * gy)
        return scaled.crop((x, y, x + box_w, y + box_h))
    if shrink_only and (tw > w or th > h):
        return im
    return im.resize((tw, th), Image.Resampling.LANCZOS)


def pad(im: Image.Image, spec: str, color: str | None, gravity: str, default: tuple[int, int, int, int]) -> Image.Image:
    kind, value = parse_pad(spec)
    w, h = im.size
    if kind == "aspect":
        cw, ch = (w, max(1, round(w / value))) if w / h > value else (max(1, round(h * value)), h)
        if cw < w or ch < h:
            cw, ch = max(cw, w), max(ch, h)
    else:
        cw, ch = value
        if cw < w or ch < h:
            raise TransformError(f"pad canvas {cw}x{ch} is smaller than the image {w}x{h} (resize first)")
    canvas = Image.new("RGBA", (cw, ch), _rgba(color, default))
    gx, gy = GRAVITY[gravity]
    rgba = im.convert("RGBA")
    canvas.alpha_composite(rgba, (round((cw - w) * gx), round((ch - h) * gy)))
    return canvas


def round_corners(im: Image.Image, spec: str) -> Image.Image:
    kind, value = parse_radius(spec)
    w, h = im.size
    radius = min(value * min(w, h) if kind == "pct" else value, min(w, h) / 2)
    scale = 4                                                    # supersample for smooth edges
    mask = Image.new("L", (w * scale, h * scale), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w * scale - 1, h * scale - 1], radius=radius * scale, fill=255)
    mask = mask.resize((w, h), Image.Resampling.LANCZOS)
    rgba = im.convert("RGBA")
    rgba.putalpha(ImageChops.multiply(rgba.getchannel("A"), mask))
    return rgba


def flatten(im: Image.Image, color: str) -> Image.Image:
    base = Image.new("RGBA", im.size, _rgba(color, (255, 255, 255, 255)))
    base.alpha_composite(im.convert("RGBA"))
    return base.convert("RGB")


def apply(im: Image.Image, ops: Ops, *, wants_alpha_default: tuple[int, int, int, int]) -> Image.Image:
    if ops.auto_orient:
        im = ImageOps.exif_transpose(im)
    if ops.trim:
        im = trim(im, ops.trim_fuzz)
    if ops.crop:
        im = crop_box(im, ops.crop)
    if ops.crop_aspect:
        im = crop_aspect(im, ops.crop_aspect, ops.gravity)
    if ops.rotate is not None:
        angle = -ops.rotate % 360
        if angle:
            im = im.convert("RGBA").rotate(angle, expand=True, resample=Image.Resampling.BICUBIC) \
                if angle % 90 else im.rotate(angle, expand=True)
    if ops.flip in ("horizontal", "both"):
        im = ImageOps.mirror(im)
    if ops.flip in ("vertical", "both"):
        im = ImageOps.flip(im)
    if ops.resize:
        im = resize(im, ops.resize, ops.resize_mode, ops.shrink_only, ops.gravity)
    if ops.pad:
        im = pad(im, ops.pad, ops.pad_color, ops.gravity, wants_alpha_default)
    if ops.round:
        im = round_corners(im, ops.round)
    if ops.background:
        im = flatten(im, ops.background)
    return im


# --------------------------------------------------------------------------- files
@dataclass
class TransformOutcome:
    input_path: Path
    output_path: Path
    status: str                     # written | skipped_existing
    input_size: tuple[int, int] = (0, 0)
    output_size: tuple[int, int] = (0, 0)
    applied: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def output_path_for(input_path: Path, ops: Ops, suffix: str, source_format: str) -> Path:
    fmt = ops.format or ("jpeg" if source_format in ("jpg", "jpeg") else source_format)
    ext = FORMATS.get(fmt, ".png")        # vector/other sources land as PNG unless told otherwise
    return input_path.with_name(f"{input_path.stem}{suffix}{ext}")


def _rasterize(path: Path, kind: str, tools: ToolPaths, dpi: float) -> Path:
    handle = tempfile.NamedTemporaryFile(suffix=".png", prefix=f"mysuite-transform-{path.stem}-", delete=False)
    handle.close()
    out = Path(handle.name)
    try:
        if kind == "svg":
            run([tools.rsvg_convert, "--format=png", f"--dpi-x={dpi}", f"--dpi-y={dpi}", f"--output={out}", str(path)])
        else:
            run([tools.gs, "-q", "-dNOPAUSE", "-dBATCH", "-dSAFER", "-sDEVICE=pngalpha", "-dFirstPage=1", "-dLastPage=1",
                 f"-r{dpi}", f"-sOutputFile={out}", str(path)])
    except FileNotFoundError as exc:
        out.unlink(missing_ok=True)
        raise TransformError(f"{kind.upper()} sources need {'rsvg-convert' if kind == 'svg' else 'Ghostscript (gs)'} installed") from exc
    return out


def transform_file(input_path: Path, ops: Ops, *, tools: ToolPaths, suffix: str = "_transformed",
                   overwrite: bool = False, dpi: float = 300.0) -> TransformOutcome:
    input_path = Path(os.path.abspath(input_path))
    kind = detect_source_format(input_path)
    if kind is None:
        raise TransformError(f"unrecognized source format: {input_path}")
    output_path = output_path_for(input_path, ops, suffix, kind)
    if output_path == input_path:
        raise TransformError("the output would replace the input - use a --suffix")
    if output_path.exists() and not overwrite:
        return TransformOutcome(input_path, output_path, "skipped_existing")

    notes: list[str] = []
    temp: Path | None = None
    try:
        source = input_path
        if kind not in RASTER_FORMATS:
            temp = source = _rasterize(input_path, kind, tools, dpi)
        try:
            with Image.open(source) as opened:
                if opened.width * opened.height > MAX_PIXELS:
                    raise TransformError(f"image is {opened.width * opened.height / 1e6:.0f} MP; the limit is {MAX_PIXELS // 1_000_000} MP")
                frames = getattr(opened, "n_frames", 1)
                if frames > 1:
                    notes.append(f"{frames} frames in the source; only the first was transformed")
                if _bit_depth(opened, source) > 8:
                    notes.append("source is more than 8 bits per channel; the output is 8-bit")
                opened.seek(0)
                icc = opened.info.get("icc_profile")
                if opened.mode == "CMYK":
                    notes.append("CMYK source converted to RGB")
                im = opened.convert("RGBA") if opened.mode in ("RGBA", "LA", "PA") or "transparency" in opened.info \
                    else opened.convert("RGB") if opened.mode in ("CMYK", "P", "1", "I", "I;16", "F") else opened.copy()
                if im.mode in ("L",):
                    im = im.convert("RGB")
                im.load()
        except TransformError:
            raise
        except Exception as exc:  # noqa: BLE001 - Pillow raises many types for corrupt files
            raise TransformError(f"can't read {input_path.name}: {exc}") from exc

        fmt = ops.format or ("jpeg" if kind in ("jpg", "jpeg") else kind if kind in FORMATS else "png")
        default_pad = (255, 255, 255, 255) if fmt == "jpeg" else (0, 0, 0, 0)
        before = im.size
        result = apply(im, ops, wants_alpha_default=default_pad)

        if fmt == "jpeg" and (result.mode == "RGBA"):
            if result.getchannel("A").getextrema()[0] < 255:
                notes.append("JPEG has no transparency: flattened on white (use background to choose)")
            result = flatten(result, ops.background or "white")
        save_kwargs: dict[str, Any] = {}
        if icc and fmt in ("png", "jpeg", "tiff", "webp"):
            save_kwargs["icc_profile"] = icc
        if ops.quality is not None and fmt in ("jpeg", "webp"):
            save_kwargs["quality"] = ops.quality
        pil_format = {"jpeg": "JPEG", "png": "PNG", "webp": "WEBP", "tiff": "TIFF"}[fmt]

        def write(tmp: Path) -> None:
            out = result.convert("RGB") if pil_format == "JPEG" and result.mode != "RGB" else result
            out.save(tmp, format=pil_format, **save_kwargs)

        atomic_write_via(output_path, write, preserve_extension=True)
    finally:
        if temp:
            temp.unlink(missing_ok=True)
    return TransformOutcome(input_path, output_path, "written", before, result.size, ops.active(), notes)
