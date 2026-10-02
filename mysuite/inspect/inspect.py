"""`mysuite inspect`: facts about an image/vector file, as numbers an agent can act on without seeing it."""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

from mysuite.color.svg import palette as svg_palette
from mysuite.config import ToolPaths
from mysuite.convert._parsing import RASTER_FORMATS, detect_source_format
from mysuite.enhance.enhance import _bit_depth
from mysuite.utils.subprocess_utils import MysuiteToolError, run

THUMB_MAX = 512
BLURRY_BELOW = 15.0   # variance of the Laplacian of the luma at <=512 px; sharp photos score in the hundreds


class InspectError(RuntimeError):
    pass


def _colour_name(mode: str) -> str:
    return {"RGB": "RGB", "RGBA": "RGB", "CMYK": "CMYK", "L": "gray", "LA": "gray", "I;16": "gray",
            "P": "palette", "1": "bilevel", "I": "gray", "F": "gray", "PA": "palette"}.get(mode, mode)


def _palette_of(image: Image.Image, n: int = 5) -> list[dict[str, Any]]:
    small = image.convert("RGBA")
    small.thumbnail((128, 128))
    arr = np.asarray(small)
    arr = arr[arr[..., 3] > 16][..., :3]
    if len(arr) == 0:
        return []
    quant = Image.fromarray(arr.reshape(-1, 1, 3)).quantize(colors=min(n, 8), method=Image.Quantize.MEDIANCUT)
    counts = sorted(quant.getcolors() or [], reverse=True)
    pal = quant.getpalette() or []
    total = sum(c for c, _ in counts) or 1
    return [{"hex": "#%02x%02x%02x" % tuple(pal[i * 3:i * 3 + 3]), "share": round(c / total, 3)} for c, i in counts[:n]]


def _sharpness(image: Image.Image) -> float:
    gray = image.convert("L")
    gray.thumbnail((THUMB_MAX, THUMB_MAX))
    a = np.asarray(gray, dtype=np.float64)
    if a.shape[0] < 3 or a.shape[1] < 3:
        return 0.0
    lap = a[:-2, 1:-1] + a[2:, 1:-1] + a[1:-1, :-2] + a[1:-1, 2:] - 4 * a[1:-1, 1:-1]
    return round(float(lap.var()), 1)


def _exif_summary(path: Path, tools: ToolPaths) -> dict[str, Any]:
    try:
        out = run([tools.exiftool, "-j", "-G1", "-n", "-q", "-m", os.path.abspath(path)]).stdout
        tags = json.loads(out)[0]
    except (MysuiteToolError, ValueError, IndexError, FileNotFoundError):
        return {}
    keys = " ".join(tags)
    return {
        "has_gps": any(k.startswith("GPS:") or k.startswith("XMP-exif:GPS") for k in tags),
        "has_serial_number": bool(re.search(r"SerialNumber", keys)),
        "camera": " ".join(str(tags[k]) for k in ("IFD0:Make", "IFD0:Model") if k in tags) or None,
        "software": tags.get("IFD0:Software"),
        "captured": tags.get("ExifIFD:DateTimeOriginal"),
        "ai_declaration": tags.get("XMP-plus:DataMining"),                # e.g. DMI-PROHIBITED-AIMLTRAINING
        "usage_terms": tags.get("XMP-xmpRights:UsageTerms"),
        "tag_count": len(tags),
    }


def _inspect_raster(path: Path, tools: ToolPaths) -> dict[str, Any]:
    try:
        with Image.open(path) as im:
            fmt, mode, size = im.format, im.mode, im.size
            dpi = im.info.get("dpi")
            frames = getattr(im, "n_frames", 1)
            orientation = (im.getexif() or {}).get(274, 1)
            depth = _bit_depth(im, path)
            icc = im.info.get("icc_profile")
            im.seek(0)
            im.load()
            rgba = im.convert("RGBA")
            alpha_present = mode in ("RGBA", "LA", "PA") or "transparency" in im.info
            alpha_min = rgba.getchannel("A").getextrema()[0] if alpha_present else 255
            upright = ImageOps.exif_transpose(im)
            info: dict[str, Any] = {
                "format": fmt, "width": size[0], "height": size[1],
                "megapixels": round(size[0] * size[1] / 1e6, 2),
                "colorspace": _colour_name(mode), "mode": mode, "bit_depth": depth,
                "dpi": [round(float(d), 1) for d in dpi] if dpi else None,
                "alpha": alpha_present, "alpha_used": alpha_present and alpha_min < 255,
                "frames": frames, "animated": frames > 1,
                "exif_orientation": int(orientation),
                "upright_width": upright.size[0], "upright_height": upright.size[1],
                "icc_profile": bool(icc),
                "dominant_colors": _palette_of(upright),
                "sharpness": _sharpness(upright),
            }
            luma = np.asarray(rgba.convert("L").resize((64, 64)), dtype=np.float64)
            info["blurry"] = info["sharpness"] < BLURRY_BELOW
            info["blank"] = bool(luma.std() < 1.5 or (alpha_present and alpha_min == 0 and np.asarray(rgba.getchannel("A")).max() == 0))
    except (UnidentifiedImageError, OSError) as exc:
        raise InspectError(f"can't read image: {exc}") from exc
    info["metadata"] = _exif_summary(path, tools)
    return info


_SVG_ROOT = re.compile(r"<svg\b[^>]*>", re.I | re.S)


def _attr(tag: str, name: str) -> str | None:
    m = re.search(rf'(?<![\w:-]){name}\s*=\s*(["\'])(.*?)\1', tag, re.S)
    return m.group(2) if m else None


def _inspect_svg(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    m = _SVG_ROOT.search(text)
    if not m:
        raise InspectError("not an SVG document (no <svg> element)")
    root = m.group(0)
    viewbox = _attr(root, "viewBox")
    fonts = sorted({f.strip().strip("'\"") for v in re.findall(r'font-family\s*[:=]\s*["\']?([^;"\'>]+)', text) for f in v.split(",") if f.strip()})
    return {
        "format": "SVG", "width": _attr(root, "width"), "height": _attr(root, "height"),
        "viewbox": [float(v) for v in re.split(r"[\s,]+", viewbox.strip())] if viewbox else None,
        "colors": [{"hex": h, "uses": n} for h, n in svg_palette(text)],
        "gradients": len(re.findall(r"<(?:linear|radial)Gradient\b", text)),
        "has_text": bool(re.search(r"<text\b", text)), "fonts": fonts,
        "embedded_images": len(re.findall(r"<image\b", text)),
        "has_filters": bool(re.search(r"<filter\b", text)),
        "external_references": bool(re.search(r'(?:xlink:)?href\s*=\s*["\'](?!#|data:)', text)),
    }


def _inspect_pdf(path: Path, tools: ToolPaths, kind: str) -> dict[str, Any]:
    out = run([tools.magick, "identify", "-format", "%w %h\n", str(path)]).stdout.split("\n")
    pages = [tuple(int(v) for v in line.split()) for line in out if line.strip()]
    raw = path.read_bytes()
    spaces = []
    if b"DeviceCMYK" in raw or re.search(rb"[\d.]+ [\d.]+ [\d.]+ [\d.]+ k\b", raw) or b"setcmykcolor" in raw:
        spaces.append("CMYK")
    if b"DeviceRGB" in raw or b"setrgbcolor" in raw or b" rg" in raw:
        spaces.append("RGB")
    return {
        "format": kind.upper(), "pages": len(pages),
        "page_size_points": list(pages[0]) if pages else None,
        "colorspaces": spaces or ["unknown (content is compressed)"],
        "output_intent": b"/OutputIntent" in raw,
    }


def inspect_file(path: Path, tools: ToolPaths) -> dict[str, Any]:
    path = Path(os.path.abspath(path))
    kind = detect_source_format(path)
    if kind is None:
        raise InspectError(f"unrecognized format: {path.suffix or 'no extension'}")
    info: dict[str, Any]
    if kind == "svg":
        info = _inspect_svg(path)
    elif kind in ("pdf", "eps"):
        info = _inspect_pdf(path, tools, kind)
    else:
        info = _inspect_raster(path, tools)
    info = {"path": str(path), "kind": kind, "bytes": path.stat().st_size, **info}
    return info


# ---------------------------------------------------------------- pictures
def _first_page_png(path: Path, kind: str, tools: ToolPaths) -> Path:
    handle = tempfile.NamedTemporaryFile(suffix=".png", prefix="mysuite-inspect-", delete=False)
    handle.close()
    out = Path(handle.name)
    if kind == "svg":
        run([tools.rsvg_convert, "--format=png", f"--width={THUMB_MAX}", "--keep-aspect-ratio", f"--output={out}", str(path)])
    else:
        run([tools.gs, "-q", "-dNOPAUSE", "-dBATCH", "-dSAFER", "-sDEVICE=png16m", "-dFirstPage=1", "-dLastPage=1",
             "-r72", f"-sOutputFile={out}", str(path)])
    return out


def thumbnail(path: Path, tools: ToolPaths, size: int = THUMB_MAX) -> Image.Image:
    kind = detect_source_format(path)
    tmp: Path | None = None
    try:
        source = path
        if kind not in RASTER_FORMATS:
            tmp = source = _first_page_png(path, kind or "", tools)
        with Image.open(source) as im:
            im.seek(0)
            im = ImageOps.exif_transpose(im).convert("RGBA")
        flat = Image.new("RGBA", im.size, (255, 255, 255, 255))
        flat.alpha_composite(im)
        flat = flat.convert("RGB")
        flat.thumbnail((size, size))
        return flat
    finally:
        if tmp:
            tmp.unlink(missing_ok=True)


def write_thumbnail(path: Path, out: Path, tools: ToolPaths) -> None:
    from mysuite.utils.subprocess_utils import atomic_write_via

    def write(tmp: Path) -> None:
        thumbnail(path, tools).save(tmp, format="PNG")

    atomic_write_via(out, write, preserve_extension=True)


def write_contact_sheet(paths: list[Path], out: Path, tools: ToolPaths, cell: int = 200, columns: int = 4) -> None:
    from mysuite.utils.subprocess_utils import atomic_write_via

    font = ImageFont.load_default()
    rows = (len(paths) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * (cell + 10) + 10, rows * (cell + 30) + 10), "#1b1d22")
    draw = ImageDraw.Draw(sheet)
    for i, p in enumerate(paths):
        x, y = 10 + (i % columns) * (cell + 10), 10 + (i // columns) * (cell + 30)
        try:
            t = thumbnail(p, tools, cell)
            sheet.paste(t, (x + (cell - t.width) // 2, y + (cell - t.height) // 2))
        except Exception:  # noqa: BLE001 - a broken file still gets a labelled empty cell
            draw.rectangle([x, y, x + cell, y + cell], outline="#F87171")
        draw.text((x, y + cell + 6), f"{i + 1}. {p.name}"[:34], fill="#e6e6e6", font=font)

    def write(tmp: Path) -> None:
        sheet.save(tmp, format="PNG")

    atomic_write_via(out, write, preserve_extension=True)
