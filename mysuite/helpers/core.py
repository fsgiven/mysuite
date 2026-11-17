"""Read-only helper tools: OCR, QR, duplicate finder, image diff, contrast check. Pure functions, no CLI."""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageOps

from mysuite.color.parse import parse_color
from mysuite.config import ToolPaths
from mysuite.utils.subprocess_utils import MysuiteToolError, atomic_write_via, run


class HelperError(ValueError):
    pass


# ------------------------------------------------------------------------------ vision (OCR / QR read)
def _vision(tools: ToolPaths, mode: str, path: Path, *extra: str) -> dict[str, Any]:
    try:
        out = run([tools.vision_tool, mode, str(Path(os.path.abspath(path))), *extra]).stdout
    except FileNotFoundError as exc:
        raise HelperError("this needs the mysuite-vision helper (macOS): build it with mysuite/native/vision/build.sh") from exc
    except MysuiteToolError as exc:
        raise HelperError((exc.stderr or str(exc)).strip().splitlines()[-1] if exc.stderr else str(exc)) from exc
    try:
        return json.loads(out)
    except ValueError as exc:
        raise HelperError("the vision helper returned something unreadable") from exc


def ocr(path: Path, tools: ToolPaths, *, languages: str | None = None, fast: bool = False) -> dict[str, Any]:
    extra: list[str] = []
    if languages:
        if not re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*(,[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*)*", languages):
            raise HelperError("languages must look like en-US,de-DE")
        extra += ["--languages", languages]
    if fast:
        extra.append("--fast")
    data = _vision(tools, "text", path, *extra)
    lines = sorted(data.get("lines", []), key=lambda l: (round(l["box"]["y"] / max(1.0, l["box"]["height"])), l["box"]["x"]))
    return {"text": "\n".join(l["text"] for l in lines), "lines": lines, "width": data.get("width"), "height": data.get("height")}


def read_codes(path: Path, tools: ToolPaths) -> list[dict[str, Any]]:
    return _vision(tools, "barcodes", path).get("codes", [])


# ------------------------------------------------------------------------------------- QR make
QR_FORMATS = {".png": "png", ".svg": "svg", ".jpg": "jpeg", ".jpeg": "jpeg", ".pdf": "pdf", ".eps": "eps"}
QR_MIN_CONTRAST = 3.0     # below this many phone cameras struggle; the spec assumes dark modules on light


def qr_colour_warnings(dark: str, light: str | None) -> list[str]:
    """Warnings for colours that scan badly: too little contrast, or light modules on a dark background."""
    d = parse_color(dark)
    l = parse_color(light) if light else None
    if d is None or l is None:
        return []
    ratio = contrast_ratio((d.r, d.g, d.b), (l.r, l.g, l.b))
    out = []
    if ratio < QR_MIN_CONTRAST:
        out.append(f"low contrast between --dark and --light ({ratio:.2f}:1): many scanners need {QR_MIN_CONTRAST:.0f}:1 "
                   "or more, so test it with a phone")
    if luminance((d.r, d.g, d.b)) > luminance((l.r, l.g, l.b)):
        out.append("--dark is lighter than --light (an inverted code): some scanners can't read it")
    return out


def make_qr(text: str, out: Path, *, fmt: str = "png", scale: int = 10, border: int = 4, error: str = "m",
            dark: str = "#000000", light: str | None = "#ffffff", overwrite: bool = False) -> dict[str, Any]:
    """Write a QR code. The output format follows the file extension (.png .svg .jpg .pdf .eps); a file without an
    extension gets `fmt`. Any other extension is refused instead of silently writing something else."""
    import segno

    if not text:
        raise HelperError("nothing to encode")
    if len(text.encode()) > 2900:
        raise HelperError("text is too long for a QR code (limit about 2900 bytes)")
    if error.lower() not in ("l", "m", "q", "h"):
        raise HelperError("error correction must be l, m, q or h")
    if not 1 <= scale <= 100 or not 0 <= border <= 20:
        raise HelperError("scale must be 1-100 and border 0-20")
    for name, value in (("dark", dark), ("light", light)):
        if value and parse_color(value) is None:
            raise HelperError(f"{name}: not a colour: {value!r}")
    target = Path(os.path.abspath(out))
    if not target.suffix:
        if fmt not in QR_FORMATS.values():
            raise HelperError("format must be png, svg, jpeg, pdf or eps")
        target = target.with_suffix("." + {"jpeg": "jpg"}.get(fmt, fmt))
    kind = QR_FORMATS.get(target.suffix.lower())
    if kind is None:
        raise HelperError(f"can't write a QR code as {target.suffix}: use .png, .svg, .jpg, .pdf or .eps")
    warnings = qr_colour_warnings(dark, light)
    if target.exists() and not overwrite:
        return {"path": target, "status": "skipped_existing", "warnings": warnings}
    qr = segno.make(text, error=error.lower(), micro=False)

    def write(tmp: Path) -> None:
        if kind == "jpeg":                         # segno has no JPEG writer: render a PNG, then encode it
            import io

            buf = io.BytesIO()
            qr.save(buf, kind="png", scale=scale, border=border, dark=dark, light=light or "#ffffff")
            buf.seek(0)
            with Image.open(buf) as im:
                im.convert("RGB").save(tmp, format="JPEG", quality=95, subsampling=0)
        else:
            qr.save(str(tmp), kind=kind, scale=scale, border=border, dark=dark, light=light)

    atomic_write_via(target, write, preserve_extension=True)
    return {"path": target, "status": "written", "format": kind, "version": qr.version,
            "modules": qr.symbol_size(border=0)[0], "warnings": warnings}


# --------------------------------------------------------------------------------- duplicates
@dataclass
class ImageFact:
    path: Path
    bytes: int
    width: int
    height: int
    sha: str
    dhash: int                 # left/right gradients, 64 bits
    vhash: int = 0             # up/down gradients, 64 bits
    informative: bool = True   # False for flat or nearly featureless pictures: their hashes match anything
    frames: int = 1


def _bits(a: np.ndarray) -> int:
    return int("".join("1" if b else "0" for b in a.flatten()), 2)


def _grey(im: Image.Image) -> Image.Image:
    """8-bit greyscale of the upright picture (16-bit is scaled, not clipped; animations use their frames' mean)."""
    from mysuite.utils import imageio

    frames = getattr(im, "n_frames", 1)
    if frames <= 1:
        return imageio.normalize(im).convert("L")
    acc = None
    picked = range(0, frames, max(1, frames // 8))           # up to ~8 frames spread over the animation
    for i in picked:
        im.seek(i)
        a = np.asarray(imageio.normalize(im, orient=False).convert("L").resize((64, 64)), dtype=np.float64)
        acc = a if acc is None else acc + a
    im.seek(0)
    return Image.fromarray((acc / len(picked)).astype(np.uint8), "L")


def _hashes(im: Image.Image) -> tuple[int, int, bool]:
    g = _grey(im)
    h = np.asarray(g.resize((9, 8), Image.Resampling.LANCZOS), dtype=np.int16)
    v = np.asarray(g.resize((8, 9), Image.Resampling.LANCZOS), dtype=np.int16)
    dh, vh = h[:, 1:] > h[:, :-1], v[1:, :] > v[:-1, :]
    small = np.asarray(g.resize((32, 32)), dtype=np.float64)
    # A flat picture, or a plain ramp, sets (almost) no bits or all of them in both directions: it would "match"
    # every other flat or ramp picture, so it only takes part in byte-identical groups.
    lopsided = lambda bits: min(int(bits.sum()), bits.size - int(bits.sum())) <= 2
    informative = small.std() >= 2.0 and not (lopsided(dh) and lopsided(vh))
    return _bits(dh), _bits(vh), informative


def _dhash(im: Image.Image) -> int:
    return _hashes(im)[0]


def fact_for(path: Path) -> ImageFact:
    data = path.read_bytes()
    try:
        with Image.open(path) as im:
            im.seek(0)
            width, height, frames = im.width, im.height, getattr(im, "n_frames", 1)
            if (ImageOps.exif_transpose(im).size != im.size):
                width, height = height, width
            dh, vh, informative = _hashes(im)
            return ImageFact(path, len(data), width, height, hashlib.sha256(data).hexdigest(), dh, vh, informative, frames)
    except Exception as exc:  # noqa: BLE001
        raise HelperError(f"can't read {path.name}: {exc}") from exc


def _similar(a: ImageFact, b: ImageFact, threshold: int) -> bool:
    if not (a.informative and b.informative):
        return False
    ra, rb = a.width / max(1, a.height), b.width / max(1, b.height)
    if abs(ra - rb) / max(ra, rb) > 0.03:                    # a resized copy keeps its shape
        return False
    return bin(a.dhash ^ b.dhash).count("1") <= threshold and bin(a.vhash ^ b.vhash).count("1") <= threshold


def find_duplicates(paths: list[Path], threshold: int = 5, limit: int = 5000) -> tuple[list[dict[str, Any]], list[tuple[Path, str]]]:
    """Groups of identical files (same bytes) and of similar pictures (perceptual hash within `threshold` bits)."""
    if not 0 <= threshold <= 20:
        raise HelperError("threshold must be 0-20 (bits of 64 that may differ)")
    if len(paths) > limit:
        raise HelperError(f"{len(paths)} files; the limit is {limit}")
    facts, errors = [], []
    for p in paths:
        try:
            facts.append(fact_for(p))
        except HelperError as exc:
            errors.append((p, str(exc)))
    parent = list(range(len(facts)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    exact_pairs: set[tuple[int, int]] = set()
    for i in range(len(facts)):
        for j in range(i + 1, len(facts)):
            same = facts[i].sha == facts[j].sha
            close = _similar(facts[i], facts[j], threshold)
            if same or close:
                parent[find(j)] = find(i)
                if same:
                    exact_pairs.add((i, j))
    groups: dict[int, list[int]] = {}
    for i in range(len(facts)):
        groups.setdefault(find(i), []).append(i)
    out = []
    for members in groups.values():
        if len(members) < 2:
            continue
        shas = {facts[i].sha for i in members}
        best = max(members, key=lambda i: (facts[i].width * facts[i].height, facts[i].bytes))
        out.append({
            "kind": "identical" if len(shas) == 1 else "similar",
            "keep_suggestion": str(facts[best].path),
            "wasted_bytes": sum(facts[i].bytes for i in members if i != best),
            "files": [{"name": facts[i].path.name, "path": str(facts[i].path), "bytes": facts[i].bytes,
                       "width": facts[i].width, "height": facts[i].height,
                       **({"frames": facts[i].frames} if facts[i].frames > 1 else {})}
                      for i in sorted(members, key=lambda i: str(facts[i].path))],
        })
    out.sort(key=lambda g: -g["wasted_bytes"])
    return out, errors


# ---------------------------------------------------------------------------------------- diff
VECTOR_KINDS = ("svg", "pdf", "eps")
DIFF_RENDER_SIZE = 1024   # SVGs are drawn this many px on their longer side; PDF/EPS pages at DIFF_RENDER_DPI
DIFF_RENDER_DPI = 150


def rasterise(path: Path, tools: ToolPaths | None) -> Image.Image:
    """Page 1 of an SVG/PDF/EPS as RGBA (SVG transparency is kept), for comparing pictures."""
    import tempfile

    from mysuite.convert._parsing import detect_source_format

    if tools is None:
        raise HelperError("comparing vector files needs the tool paths")
    kind = detect_source_format(path)
    handle = tempfile.NamedTemporaryFile(suffix=".png", prefix="mysuite-diff-", delete=False)
    handle.close()
    png = Path(handle.name)
    try:
        if kind == "svg":
            run([tools.rsvg_convert, "--format=png", f"--width={DIFF_RENDER_SIZE}", "--keep-aspect-ratio",
                 f"--output={png}", os.path.abspath(path)])
        else:
            run([tools.gs, "-q", "-dNOPAUSE", "-dBATCH", "-dSAFER", "-sDEVICE=pngalpha", "-dFirstPage=1", "-dLastPage=1",
                 *(["-dEPSCrop"] if kind == "eps" else []), f"-r{DIFF_RENDER_DPI}", f"-sOutputFile={png}", os.path.abspath(path)])
        with Image.open(png) as im:
            return im.convert("RGBA")
    except FileNotFoundError as exc:
        raise HelperError(f"rendering {path.name} needs {'rsvg-convert' if kind == 'svg' else 'Ghostscript (gs)'}") from exc
    except MysuiteToolError as exc:
        raise HelperError(f"can't render {path.name}: {(exc.stderr or str(exc)).strip()[:200]}") from exc
    finally:
        png.unlink(missing_ok=True)


def _load_for_diff(path: Path, tools: ToolPaths | None) -> tuple[Image.Image, str | None]:
    from mysuite.convert._parsing import detect_source_format
    from mysuite.utils import imageio

    kind = detect_source_format(path)
    if kind in VECTOR_KINDS:
        how = f"{DIFF_RENDER_SIZE} px on the longer side" if kind == "svg" else f"page 1 at {DIFF_RENDER_DPI} dpi"
        return rasterise(path, tools), f"{path.name} was rendered ({how}) for the comparison"
    with Image.open(path) as im:
        im.seek(0)
        return imageio.normalize(im).convert("RGBA"), None


def diff_images(a: Path, b: Path, *, threshold: int = 16, out: Path | None = None, overwrite: bool = False,
                tools: ToolPaths | None = None) -> dict[str, Any]:
    """Compare two pictures. SVG/PDF/EPS are rendered first (needs `tools`); rasters are compared as stored, upright."""
    if not 0 <= threshold <= 255:
        raise HelperError("threshold must be 0-255")
    notes: list[str] = []
    try:
        ra, na = _load_for_diff(a, tools)
        rb, nb = _load_for_diff(b, tools)
    except HelperError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HelperError(f"can't read the images: {exc}") from exc
    notes += [n for n in (na, nb) if n]
    same_size = ra.size == rb.size
    note = None
    if not same_size:
        note = f"sizes differ ({ra.width}x{ra.height} vs {rb.width}x{rb.height}): the second was resized to the first for comparison"
        rb = rb.resize(ra.size, Image.Resampling.LANCZOS)
    white = Image.new("RGBA", ra.size, "white")
    fa = np.asarray(Image.alpha_composite(white, ra).convert("RGB"), dtype=np.int32)
    fb = np.asarray(Image.alpha_composite(white, rb).convert("RGB"), dtype=np.int32)
    delta = np.abs(fa - fb).max(axis=2)
    mse = float(((fa - fb) ** 2).mean())
    result = {
        "same_size": same_size, "width": ra.width, "height": ra.height,
        "identical": bool(delta.max() == 0), "mean_abs_diff": round(float(np.abs(fa - fb).mean()), 3),
        "changed_pct": round(float((delta > threshold).mean() * 100), 3), "max_diff": int(delta.max()),
        "psnr_db": None if mse == 0 else round(10 * np.log10(255 ** 2 / mse), 2), "threshold": threshold, "note": note,
        "notes": notes,
    }
    if out is not None:
        target = Path(os.path.abspath(out))
        if target.exists() and not overwrite:
            result["diff_image"] = {"path": str(target), "status": "skipped_existing"}
        else:
            base = (fa * 0.35 + 255 * 0.65).astype(np.uint8)
            base[delta > threshold] = (255, 0, 60)
            image = Image.fromarray(base, "RGB")

            def write(tmp: Path) -> None:
                image.save(tmp, format="PNG")

            atomic_write_via(target, write, preserve_extension=True)
            result["diff_image"] = {"path": str(target), "status": "written"}
    return result


# ----------------------------------------------------------------------------------- contrast
def _channel(c: int) -> float:
    c /= 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(rgb: tuple[int, int, int]) -> float:
    r, g, b = (_channel(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def grade(ratio: float) -> dict[str, bool]:
    return {"AA_normal_text": ratio >= 4.5, "AA_large_text": ratio >= 3.0, "AAA_normal_text": ratio >= 7.0,
            "AAA_large_text": ratio >= 4.5, "graphics_and_ui_AA": ratio >= 3.0}


def check_colours(fg: str, bg: str) -> dict[str, Any]:
    f, b = parse_color(fg), parse_color(bg)
    if f is None or b is None:
        raise HelperError(f"not a colour: {fg if f is None else bg!r}")
    if (f.alpha is not None and f.alpha < 1) or (b.alpha is not None and b.alpha < 1):
        raise HelperError("colours with transparency have no single contrast: pass opaque colours")
    ratio = contrast_ratio((f.r, f.g, f.b), (b.r, b.g, b.b))
    return {"foreground": f.hex(), "background": b.hex(), "ratio": round(ratio, 2), **grade(ratio)}
