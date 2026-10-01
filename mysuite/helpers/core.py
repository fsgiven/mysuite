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
def make_qr(text: str, out: Path, *, fmt: str = "png", scale: int = 10, border: int = 4, error: str = "m",
            dark: str = "#000000", light: str | None = "#ffffff", overwrite: bool = False) -> dict[str, Any]:
    import segno

    if not text:
        raise HelperError("nothing to encode")
    if len(text.encode()) > 2900:
        raise HelperError("text is too long for a QR code (limit about 2900 bytes)")
    if error.lower() not in ("l", "m", "q", "h"):
        raise HelperError("error correction must be l, m, q or h")
    if fmt not in ("png", "svg"):
        raise HelperError("format must be png or svg")
    if not 1 <= scale <= 100 or not 0 <= border <= 20:
        raise HelperError("scale must be 1-100 and border 0-20")
    for name, value in (("dark", dark), ("light", light)):
        if value and parse_color(value) is None:
            raise HelperError(f"{name}: not a colour: {value!r}")
    target = Path(os.path.abspath(out))
    if target.suffix.lower() not in (".png", ".svg"):
        target = target.with_suffix("." + fmt)
    fmt = target.suffix.lower().lstrip(".")
    if target.exists() and not overwrite:
        return {"path": target, "status": "skipped_existing"}
    qr = segno.make(text, error=error.lower(), micro=False)

    def write(tmp: Path) -> None:
        qr.save(str(tmp), kind=fmt, scale=scale, border=border, dark=dark, light=light)

    atomic_write_via(target, write, preserve_extension=True)
    return {"path": target, "status": "written", "version": qr.version, "modules": qr.symbol_size(border=0)[0]}


# --------------------------------------------------------------------------------- duplicates
@dataclass
class ImageFact:
    path: Path
    bytes: int
    width: int
    height: int
    sha: str
    dhash: int


def _dhash(im: Image.Image) -> int:
    g = ImageOps.exif_transpose(im).convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    a = np.asarray(g, dtype=np.int16)
    bits = (a[:, 1:] > a[:, :-1]).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


def fact_for(path: Path) -> ImageFact:
    data = path.read_bytes()
    try:
        with Image.open(path) as im:
            im.seek(0)
            return ImageFact(path, len(data), im.width, im.height, hashlib.sha256(data).hexdigest(), _dhash(im))
    except Exception as exc:  # noqa: BLE001
        raise HelperError(f"can't read {path.name}: {exc}") from exc


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
            close = bin(facts[i].dhash ^ facts[j].dhash).count("1") <= threshold
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
            "files": [{"path": str(facts[i].path), "bytes": facts[i].bytes, "width": facts[i].width, "height": facts[i].height}
                      for i in sorted(members, key=lambda i: str(facts[i].path))],
        })
    out.sort(key=lambda g: -g["wasted_bytes"])
    return out, errors


# ---------------------------------------------------------------------------------------- diff
def diff_images(a: Path, b: Path, *, threshold: int = 16, out: Path | None = None, overwrite: bool = False) -> dict[str, Any]:
    if not 0 <= threshold <= 255:
        raise HelperError("threshold must be 0-255")
    try:
        with Image.open(a) as ia, Image.open(b) as ib:
            ia.seek(0)
            ib.seek(0)
            ra, rb = ImageOps.exif_transpose(ia).convert("RGBA"), ImageOps.exif_transpose(ib).convert("RGBA")
    except Exception as exc:  # noqa: BLE001
        raise HelperError(f"can't read the images: {exc}") from exc
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
