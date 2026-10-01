"""Exact-spec jobs: print-size output, rename by pattern, contact sheets, colour-profile conversion."""
from __future__ import annotations

import io
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageCms, ImageDraw, ImageFont, ImageOps

from mysuite.color.cmyk import CmykEngine, CmykSettings
from mysuite.color.parse import parse_color
from mysuite.config import ToolPaths
from mysuite.convert._parsing import RASTER_FORMATS, detect_source_format
from mysuite.enhance.enhance import _bit_depth
from mysuite.utils.subprocess_utils import atomic_write_via, run

MAX_PIXELS = 200_000_000


class SpecError(ValueError):
    pass


# ------------------------------------------------------------------------------------- sizes
_UNIT_PX = {"px": None, "mm": 25.4, "cm": 2.54, "in": 1.0}
_SIZE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)\s*(px|mm|cm|in)?\s*$", re.I)


def parse_size(spec: str, dpi: float) -> tuple[int, int, str]:
    """'10x15cm' / '4x6in' / '1200x630' (pixels) -> (width_px, height_px, unit)."""
    m = _SIZE.match(spec)
    if not m:
        raise SpecError(f"size must look like 10x15cm, 4x6in, 210x297mm or 1200x630px, got {spec!r}")
    unit = (m.group(3) or "px").lower()
    w, h = float(m.group(1)), float(m.group(2))
    if unit != "px":
        w, h = w / _UNIT_PX[unit] * dpi, h / _UNIT_PX[unit] * dpi
    wp, hp = round(w), round(h)
    if wp < 1 or hp < 1:
        raise SpecError("size is too small")
    if wp * hp > MAX_PIXELS:
        raise SpecError(f"{wp}x{hp} px is too large (limit {MAX_PIXELS // 1_000_000} MP)")
    return wp, hp, unit


def _fill(color: str | None, default: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    if not color:
        return default
    c = parse_color(color)
    if c is None:
        raise SpecError(f"not a colour: {color!r}")
    return c.r, c.g, c.b, 255 if c.alpha is None else round(c.alpha * 255)


# -------------------------------------------------------------------------------------- print
FORMATS = {"png": ("PNG", ".png"), "jpeg": ("JPEG", ".jpg"), "tiff": ("TIFF", ".tiff"), "webp": ("WEBP", ".webp")}


@dataclass
class PrintResult:
    path: Path
    status: str
    size: tuple[int, int] = (0, 0)
    dpi: float = 0
    notes: list[str] = field(default_factory=list)


def _load_raster(path: Path, tools: ToolPaths, target: tuple[int, int], fit: str) -> Image.Image:
    kind = detect_source_format(path)
    if kind is None:
        raise SpecError(f"unrecognized format: {path.suffix or 'no extension'}")
    tmp: Path | None = None
    try:
        source = path
        if kind not in RASTER_FORMATS:
            handle = tempfile.NamedTemporaryFile(suffix=".png", prefix="mysuite-print-", delete=False)
            handle.close()
            tmp = source = Path(handle.name)
            try:
                if kind == "svg":
                    run([tools.rsvg_convert, "--format=png", "--width=800", f"--output={tmp}", str(path)])
                    with Image.open(tmp) as probe:
                        ratio = probe.width / probe.height
                    want = max(target[0], round(target[1] * ratio)) if fit == "cover" else min(target[0], round(target[1] * ratio))
                    run([tools.rsvg_convert, "--format=png", f"--width={max(1, want)}", f"--output={tmp}", str(path)])
                else:
                    run([tools.gs, "-q", "-dNOPAUSE", "-dBATCH", "-dSAFER", "-sDEVICE=pngalpha", "-dFirstPage=1", "-dLastPage=1",
                         "-r300", f"-sOutputFile={tmp}", str(path)])
            except FileNotFoundError as exc:
                raise SpecError(f"{kind.upper()} sources need rsvg-convert / Ghostscript installed") from exc
        try:
            with Image.open(source) as im:
                if im.width * im.height > MAX_PIXELS:
                    raise SpecError("source image is too large")
                im.seek(0)
                out = ImageOps.exif_transpose(im).convert("RGBA")
                out.info["bit_depth"] = _bit_depth(im, source)
                return out
        except SpecError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SpecError(f"can't read {path.name}: {exc}") from exc
    finally:
        if tmp:
            tmp.unlink(missing_ok=True)


def print_size(path: Path, size: str, *, dpi: float = 300.0, fit: str = "contain", background: str | None = None,
               fmt: str | None = None, quality: int = 95, gravity: str = "center", bleed_mm: float = 0.0,
               auto_rotate: bool = False, tools: ToolPaths, overwrite: bool = False) -> PrintResult:
    """Output at EXACTLY the requested pixel size (physical sizes via dpi), with the dpi written into the file."""
    if not 36 <= dpi <= 2400:
        raise SpecError("dpi must be between 36 and 2400")
    if fit not in ("contain", "cover", "stretch"):
        raise SpecError("fit must be contain, cover or stretch")
    if fmt not in (None, *FORMATS):
        raise SpecError(f"format must be one of {', '.join(FORMATS)}")
    if not 1 <= quality <= 100 or not 0 <= bleed_mm <= 50:
        raise SpecError("quality must be 1-100 and bleed 0-50 mm")
    from mysuite.transform.transform import GRAVITY

    if gravity not in GRAVITY:
        raise SpecError(f"unknown gravity {gravity!r} - expected one of {', '.join(GRAVITY)}")
    width, height, unit = parse_size(size, dpi)
    bleed = round(bleed_mm / 25.4 * dpi)
    path = Path(os.path.abspath(path))
    kind = detect_source_format(path)
    fmt = fmt or ("jpeg" if kind in ("jpg", "jpeg") else kind if kind in FORMATS else "png")
    out = path.with_name(f"{path.stem}_print{FORMATS[fmt][1]}")
    if out == path:
        raise SpecError("the output would replace the input")
    if out.exists() and not overwrite:
        return PrintResult(out, "skipped_existing")
    fill = _fill(background, (255, 255, 255, 255) if fmt == "jpeg" else (0, 0, 0, 0))
    total_w, total_h = width + 2 * bleed, height + 2 * bleed
    notes: list[str] = []
    image = _load_raster(path, tools, (total_w, total_h), fit)
    if image.info.get("bit_depth", 8) > 8:
        notes.append("source is more than 8 bits per channel; the output is 8-bit")
    if auto_rotate and (image.width > image.height) != (total_w > total_h) and fit != "stretch":
        image = image.rotate(90, expand=True)
        notes.append("image turned 90 degrees to match the paper orientation")
    from mysuite.transform.transform import GRAVITY as G

    gx, gy = G[gravity]
    if fit == "stretch":
        fitted = image.resize((total_w, total_h), Image.Resampling.LANCZOS)
        canvas = fitted
    elif fit == "cover":
        scale = max(total_w / image.width, total_h / image.height)
        sw, sh = max(total_w, round(image.width * scale)), max(total_h, round(image.height * scale))
        scaled = image.resize((sw, sh), Image.Resampling.LANCZOS)
        x, y = round((sw - total_w) * gx), round((sh - total_h) * gy)
        canvas = scaled.crop((x, y, x + total_w, y + total_h))
    else:
        scale = min(total_w / image.width, total_h / image.height)
        fitted = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (total_w, total_h), fill)
        canvas.alpha_composite(fitted, (round((total_w - fitted.width) * gx), round((total_h - fitted.height) * gy)))
    if fmt == "jpeg" or (background and fill[3] == 255):
        flat = Image.new("RGBA", canvas.size, fill if fill[3] == 255 else (255, 255, 255, 255))
        flat.alpha_composite(canvas)
        canvas = flat.convert("RGB")
    pil_format = FORMATS[fmt][0]
    kwargs: dict[str, Any] = {"dpi": (dpi, dpi)}
    if pil_format == "JPEG":
        kwargs.update(quality=quality, subsampling=0)
        canvas = canvas.convert("RGB")
    if pil_format == "TIFF":
        kwargs["compression"] = "tiff_lzw"

    def write(tmp: Path) -> None:
        canvas.save(tmp, format=pil_format, **kwargs)

    atomic_write_via(out, write, preserve_extension=True)
    if bleed:
        notes.append(f"includes {bleed_mm:g} mm bleed on every side ({total_w}x{total_h} px in total)")
    return PrintResult(out, "written", (total_w, total_h), dpi, notes)


# ------------------------------------------------------------------------------------- rename
_TOKEN = re.compile(r"\{(name|ext|n|date|datetime|w|h|upper|lower)(?::(\d+))?\}")


def _capture_time(path: Path) -> datetime:
    try:
        with Image.open(path) as im:
            exif = im.getexif()
            raw = exif.get_ifd(0x8769).get(36867) or exif.get(306)
            if raw:
                return datetime.strptime(str(raw), "%Y:%m:%d %H:%M:%S")
    except Exception:  # noqa: BLE001 - not an image, or no EXIF: fall back to the file time
        pass
    return datetime.fromtimestamp(path.stat().st_mtime)


def _dims(path: Path) -> tuple[int, int]:
    try:
        with Image.open(path) as im:
            return im.width, im.height
    except Exception:  # noqa: BLE001
        return 0, 0


def new_name(pattern: str, path: Path, n: int, when: datetime) -> str:
    def repl(m: re.Match[str]) -> str:
        tok, width = m.group(1), m.group(2)
        if tok == "name":
            return path.stem
        if tok == "ext":
            return path.suffix.lower()
        if tok == "n":
            return str(n).zfill(int(width) if width else 1)
        if tok == "date":
            return when.strftime("%Y-%m-%d")
        if tok == "datetime":
            return when.strftime("%Y-%m-%d_%H%M%S")
        w, h = _dims(path)
        if tok == "w":
            return str(w)
        if tok == "h":
            return str(h)
        return m.group(0)

    name = _TOKEN.sub(repl, pattern)
    if "{" in name or "}" in name:
        raise SpecError(f"unknown token in the pattern {pattern!r}: use {{name}} {{ext}} {{n}} {{n:3}} {{date}} {{datetime}} {{w}} {{h}}")
    if not name or "/" in name or "\\" in name or name in (".", "..") or "\0" in name:
        raise SpecError(f"the pattern produces an unusable name: {name!r}")
    if len(name) > 200:
        raise SpecError("the pattern produces a name that is too long")
    return name


@dataclass
class RenamePlan:
    source: Path
    target: Path
    status: str = "planned"            # planned | written | skipped_existing | failed
    error: str | None = None


def plan_rename(files: list[Path], pattern: str, *, start: int = 1, sort: str = "name", out_dir: Path | None = None) -> list[RenamePlan]:
    if sort not in ("name", "date", "size"):
        raise SpecError("sort must be name, date or size")
    if "{ext}" not in pattern and not Path(pattern).suffix:
        pattern += "{ext}"
    key = {"name": lambda p: p.name.lower(), "date": lambda p: (_capture_time(p), p.name.lower()), "size": lambda p: (p.stat().st_size, p.name.lower())}[sort]
    ordered = sorted(files, key=key)
    plans, seen = [], {}
    for i, f in enumerate(ordered, start=start):
        name = new_name(pattern, f, i, _capture_time(f))
        folder = Path(os.path.abspath(out_dir)) if out_dir else f.parent
        target = folder / name
        if target in seen:
            raise SpecError(f"{f.name} and {seen[target].name} would both become {name!r}: add {{n}} to the pattern")
        seen[target] = f
        plans.append(RenamePlan(f, target))
    return plans


def run_rename(plans: list[RenamePlan], *, move: bool, overwrite: bool) -> list[RenamePlan]:
    sources = {p.source for p in plans}
    for p in plans:
        if p.target.exists() and not overwrite and p.target not in sources:
            p.status = "skipped_existing"
    if move:
        # two-phase so that a->b, b->a style swaps cannot clobber each other
        staged: list[tuple[RenamePlan, Path]] = []
        for p in plans:
            if p.status != "planned" or p.source == p.target:
                if p.source == p.target:
                    p.status = "skipped_existing"
                continue
            tmp = p.source.with_name(f".mysuite-rename-{os.getpid()}-{len(staged)}")
            os.rename(p.source, tmp)
            staged.append((p, tmp))
        for p, tmp in staged:
            p.target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(tmp, p.target)
            p.status = "written"
    else:
        for p in plans:
            if p.status != "planned":
                continue
            from mysuite import sandbox

            sandbox.check_write(p.target)

            def write(tmp: Path, src: Path = p.source) -> None:
                shutil.copyfile(src, tmp)

            atomic_write_via(p.target, write, preserve_extension=True)
            p.status = "written"
    return plans


# ------------------------------------------------------------------------------------- sheet
def contact_sheet(paths: list[Path], out: Path, tools: ToolPaths, *, columns: int = 4, cell: int = 240, labels: bool = True,
                  background: str = "#1b1d22", title: str | None = None, overwrite: bool = False) -> dict[str, Any]:
    from mysuite.inspect.inspect import thumbnail

    if not paths:
        raise SpecError("no images")
    if not 1 <= columns <= 20 or not 32 <= cell <= 1200:
        raise SpecError("columns must be 1-20 and cell 32-1200 px")
    if len(paths) > 600:
        raise SpecError("too many images for one sheet (limit 600)")
    bg = _fill(background, (27, 29, 34, 255))
    target = Path(os.path.abspath(out))
    if target.suffix.lower() not in (".png", ".pdf", ".jpg", ".jpeg"):
        raise SpecError("the sheet must be a .png, .jpg or .pdf file")
    if target.exists() and not overwrite:
        return {"path": target, "status": "skipped_existing"}
    pad, label_h = 12, (22 if labels else 0)
    head = 36 if title else 0
    rows = (len(paths) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * (cell + pad) + pad, head + rows * (cell + label_h + pad) + pad), bg[:3])
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    lum = 0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2]
    ink = "#111111" if lum > 140 else "#e6e6e6"
    if title:
        draw.text((pad, 12), title, fill=ink, font=font)
    failed: list[str] = []
    for i, p in enumerate(paths):
        x, y = pad + (i % columns) * (cell + pad), head + pad + (i // columns) * (cell + label_h + pad)
        try:
            t = thumbnail(p, tools, cell)
            sheet.paste(t, (x + (cell - t.width) // 2, y + (cell - t.height) // 2))
        except Exception:  # noqa: BLE001 - one bad file must not sink the sheet
            draw.rectangle([x, y, x + cell, y + cell], outline="#F87171")
            failed.append(p.name)
        if labels:
            draw.text((x, y + cell + 5), f"{i + 1}. {p.name}"[: max(8, cell // 6)], fill=ink, font=font)
    fmt = {".png": "PNG", ".pdf": "PDF", ".jpg": "JPEG", ".jpeg": "JPEG"}[target.suffix.lower()]

    def write(tmp: Path) -> None:
        sheet.save(tmp, format=fmt, **({"resolution": 150.0} if fmt == "PDF" else {}))

    atomic_write_via(target, write, preserve_extension=True)
    return {"path": target, "status": "written", "size": sheet.size, "images": len(paths), "failed": failed}


# ----------------------------------------------------------------------------- colour profiles
def convert_profile(path: Path, to: str, *, tools: ToolPaths, cmyk_mode: str = "exact", cmyk_profile: str | None = None,
                    fmt: str | None = None, quality: int = 95, overwrite: bool = False) -> PrintResult:
    if to not in ("srgb", "cmyk"):
        raise SpecError("to must be srgb or cmyk")
    path = Path(os.path.abspath(path))
    if detect_source_format(path) not in RASTER_FORMATS:
        raise SpecError("profile conversion works on raster images")
    fmt = fmt or ("tiff" if to == "cmyk" else ("jpeg" if path.suffix.lower() in (".jpg", ".jpeg") else "png"))
    if fmt not in FORMATS or (to == "cmyk" and fmt not in ("tiff", "jpeg")):
        raise SpecError("cmyk output must be tiff or jpeg" if to == "cmyk" else f"format must be one of {', '.join(FORMATS)}")
    out = path.with_name(f"{path.stem}_{to}{FORMATS[fmt][1]}")
    if out == path:
        raise SpecError("the output would replace the input")
    if out.exists() and not overwrite:
        return PrintResult(out, "skipped_existing")
    notes: list[str] = []
    try:
        with Image.open(path) as opened:
            opened.seek(0)
            icc = opened.info.get("icc_profile")
            mode = opened.mode
            src = ImageOps.exif_transpose(opened)
            src.load()
            alpha = src.getchannel("A") if src.mode in ("RGBA", "LA") else None
            base = src.convert("RGB") if src.mode not in ("CMYK",) else src.copy()
    except Exception as exc:  # noqa: BLE001
        raise SpecError(f"can't read {path.name}: {exc}") from exc
    srgb = ImageCms.createProfile("sRGB")
    if to == "srgb":
        if base.mode == "CMYK":
            engine = CmykEngine(CmykSettings.parse("exact", cmyk_profile), gs_binary=tools.gs)
            profile = ImageCms.ImageCmsProfile(io.BytesIO(icc)) if icc else engine._cmyk
            if not icc:
                notes.append(f"no embedded profile: read as {engine.description}")
            result = ImageCms.profileToProfile(base, profile, srgb, outputMode="RGB", renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC)
        elif icc:
            try:
                result = ImageCms.profileToProfile(base, ImageCms.ImageCmsProfile(io.BytesIO(icc)), srgb, outputMode="RGB")
            except ImageCms.PyCMSError as exc:
                raise SpecError(f"the embedded profile can't be used: {exc}") from exc
        else:
            result = base
            notes.append("no embedded profile: assumed to be sRGB already, so the pixels are unchanged")
        if alpha is not None and fmt != "jpeg":
            result = result.convert("RGBA")
            result.putalpha(alpha)
        icc_out = ImageCms.ImageCmsProfile(srgb).tobytes()
    else:
        settings = CmykSettings.parse(cmyk_mode, cmyk_profile)
        engine = CmykEngine(settings, gs_binary=tools.gs)
        rgb = base
        if base.mode == "CMYK":
            raise SpecError("the image is already CMYK")
        if icc:                                   # honour the source profile before the CMYK conversion
            try:
                rgb = ImageCms.profileToProfile(base, ImageCms.ImageCmsProfile(io.BytesIO(icc)), srgb, outputMode="RGB")
            except ImageCms.PyCMSError:
                notes.append("the embedded profile couldn't be used: treated as sRGB")
        if alpha is not None:
            flat = Image.new("RGB", rgb.size, "white")
            flat.paste(rgb, mask=alpha)
            rgb = flat
            notes.append("CMYK files have no transparency: flattened on white")
        result = engine.convert_image(rgb)
        icc_out = engine.icc_bytes
        notes.append(f"{settings.mode}{':' + str(settings.step) if settings.mode == 'clean' else ''} via {engine.description}")
    pil_format = FORMATS[fmt][0]
    kwargs: dict[str, Any] = {"icc_profile": icc_out}
    if pil_format == "JPEG":
        kwargs["quality"] = quality
        if result.mode == "RGBA":
            result = result.convert("RGB")
    if pil_format == "TIFF":
        kwargs["compression"] = "tiff_lzw"

    def write(tmp: Path) -> None:
        result.save(tmp, format=pil_format, **kwargs)

    atomic_write_via(out, write, preserve_extension=True)
    return PrintResult(out, "written", result.size, 0, notes)
