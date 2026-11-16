"""Shape variants of a logo that sits on a background shape (a red square with white letters, a badge…):

* ``knockout``  the background shape stays and everything on it is punched out as holes (the brand "negative"
                that works on any surface: one colour, see-through letters).
* ``cutout``    the background shape is removed and everything on it stays.

The background is found by rendering, not by trusting the markup: it is the first painted shape, and everything
painted after it must lie inside it. A knockout is written as one even-odd path when every shape is a plain
path/rect/circle/ellipse/polygon in the same coordinate system (clean vector: flat CMYK, like a hand-made
negative); the result is checked by rendering, and anything that does not match falls back to an SVG mask.
"""
from __future__ import annotations

import copy
import io
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET

from mysuite.color.variants import VariantError

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)

PAINTED = {"path", "rect", "circle", "ellipse", "polygon", "polyline", "line", "text", "use", "image"}
NOT_RENDERED = {"defs", "clipPath", "mask", "pattern", "symbol", "marker", "linearGradient", "radialGradient",
                "filter", "title", "desc", "metadata", "style"}
RENDER_PX = 256
INSIDE_MIN = 0.97        # share of the foreground that must lie on the background shape
MISMATCH_MAX = 0.003     # share of pixels a merged path may differ from the expected knockout


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _painted(root: ET.Element) -> list[tuple[ET.Element, list[ET.Element]]]:
    """Painted shapes in drawing order, each with its ancestor chain (root excluded)."""
    out: list[tuple[ET.Element, list[ET.Element]]] = []

    def walk(el: ET.Element, chain: list[ET.Element]) -> None:
        for child in el:
            name = _local(child.tag)
            if name in NOT_RENDERED or child.get("display") == "none":
                continue
            if name in PAINTED:
                out.append((child, chain))
            else:
                walk(child, chain + [child])

    walk(root, [])
    return out


def _parent_map(root: ET.Element) -> dict[ET.Element, ET.Element]:
    return {c: p for p in root.iter() for c in p}


def _rsvg() -> str:
    exe = shutil.which("rsvg-convert")
    if not exe:
        raise VariantError("knockout/cutout need rsvg-convert (brew install librsvg)")
    return exe


def _alpha(svg: str):
    import numpy as np
    from PIL import Image

    png = subprocess.run([_rsvg(), "-w", str(RENDER_PX), "-h", str(RENDER_PX), "--keep-aspect-ratio"],
                         input=svg.encode("utf-8"), capture_output=True)
    if png.returncode != 0:
        raise VariantError(f"could not render the logo: {png.stderr.decode(errors='replace').strip()[:200]}")
    return np.asarray(Image.open(io.BytesIO(png.stdout)).convert("RGBA"), dtype=np.float32)[..., 3] / 255.0


def _only(root: ET.Element, keep: set[int], shapes: list[ET.Element]) -> str:
    """The document with every painted shape hidden except those whose index is in `keep`."""
    saved = [s.get("display") for s in shapes]
    for i, s in enumerate(shapes):
        if i not in keep:
            s.set("display", "none")
    try:
        return ET.tostring(root, encoding="unicode")
    finally:
        for s, d in zip(shapes, saved):
            if d is None:
                s.attrib.pop("display", None)
            else:
                s.set("display", d)


def _parse(text: str) -> ET.Element:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise VariantError(f"the SVG could not be read: {exc}") from exc
    if _local(root.tag) != "svg":
        raise VariantError("not an SVG document")
    return root


def find_background(text: str):
    """(root, shapes, background alpha, foreground alpha). Raises VariantError when the logo has no background shape."""
    root = _parse(text)
    found = _painted(root)
    shapes = [s for s, _ in found]
    if len(shapes) < 2:
        raise VariantError("knockout/cutout need a background shape with something on it; this logo has "
                           f"{len(shapes)} shape(s)")
    bg = _alpha(_only(root, {0}, shapes)) > 0.5
    fg = _alpha(_only(root, set(range(1, len(shapes))), shapes)) > 0.5
    if bg.mean() < 0.05 or not fg.any():
        raise VariantError("no background shape found: the first shape drawn is too small to hold the rest")
    inside = float((fg & bg).sum() / fg.sum())
    if inside < INSIDE_MIN:
        raise VariantError(f"no background shape found: only {inside:.0%} of the logo lies on the first shape drawn "
                           "(knockout/cutout need a logo that sits on one background shape)")
    return root, found, bg, fg


# --------------------------------------------------------------------------- path data
_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"


def _num(el: ET.Element, name: str, default: float | None = 0.0) -> float | None:
    raw = (el.get(name) or "").strip()
    if not raw:
        return default
    m = re.fullmatch(rf"({_NUM})(px)?", raw)
    return float(m.group(1)) if m else None


def _path_data(el: ET.Element) -> str | None:
    """Path data for a simple shape, or None when it can't be expressed exactly."""
    name = _local(el.tag)
    if name == "path":
        return el.get("d")
    if name == "rect":
        x, y, w, h = (_num(el, a) for a in ("x", "y", "width", "height"))
        if None in (x, y, w, h) or el.get("rx") or el.get("ry"):
            return None
        return f"M{x:g} {y:g}H{x + w:g}V{y + h:g}H{x:g}Z"
    if name in ("circle", "ellipse"):
        cx, cy = _num(el, "cx"), _num(el, "cy")
        rx = _num(el, "r") if name == "circle" else _num(el, "rx")
        ry = rx if name == "circle" else _num(el, "ry")
        if None in (cx, cy, rx, ry):
            return None
        return (f"M{cx - rx:g} {cy:g}A{rx:g} {ry:g} 0 1 0 {cx + rx:g} {cy:g}"
                f"A{rx:g} {ry:g} 0 1 0 {cx - rx:g} {cy:g}Z")
    if name == "polygon":
        pts = re.findall(_NUM, el.get("points") or "")
        if len(pts) < 6 or len(pts) % 2:
            return None
        pairs = [f"{pts[i]} {pts[i + 1]}" for i in range(0, len(pts), 2)]
        return "M" + "L".join(pairs) + "Z"
    return None


def _strokes(el: ET.Element) -> bool:
    stroke = el.get("stroke") or ""
    style = el.get("style") or ""
    m = re.search(r"stroke\s*:\s*([^;]+)", style)
    value = (m.group(1) if m else stroke).strip().lower()
    return bool(value) and value != "none"


def _fill_none(el: ET.Element) -> bool:
    style = el.get("style") or ""
    m = re.search(r"fill\s*:\s*([^;]+)", style)
    return (m.group(1) if m else el.get("fill") or "").strip().lower() == "none"


# ------------------------------------------------------------------------------ variants
def _merged(root: ET.Element, found) -> str | None:
    """One even-odd path (background minus everything on it), or None if the shapes can't be merged."""
    bg_el, bg_chain = found[0]
    datas = []
    for el, chain in found:
        if chain != bg_chain or el.get("transform") or _strokes(el) or (el is not bg_el and _fill_none(el)):
            return None
        d = _path_data(el)
        if not d:
            return None
        datas.append(d.strip())
    work = copy.deepcopy(root)
    shapes = [s for s, _ in _painted(work)]
    parents = _parent_map(work)
    first = shapes[0]
    merged = ET.Element(f"{{{SVG_NS}}}path")
    for k, v in first.attrib.items():
        if k not in ("d", "fill-rule", "clip-rule", "x", "y", "width", "height", "cx", "cy", "r", "rx", "ry", "points"):
            merged.set(k, v)
    if first.get("style"):
        merged.set("style", re.sub(r"fill-rule\s*:[^;]*;?", "", first.get("style")))
    merged.set("d", " ".join(datas))
    merged.set("fill-rule", "evenodd")
    parent = parents[first]
    parent.insert(list(parent).index(first), merged)
    for s in shapes:
        parents[s].remove(s)
    return ET.tostring(work, encoding="unicode")


def _masked(root: ET.Element) -> str:
    """Background shape shown through a mask in which everything on it is black: works for any geometry."""
    work = copy.deepcopy(root)
    found = _painted(work)
    shapes = [s for s, _ in found]
    vb = [float(v) for v in re.findall(_NUM, work.get("viewBox") or "")]
    if len(vb) == 4:
        x, y, w, h = vb
    else:
        x, y, w, h = 0.0, 0.0, _num(work, "width", 100.0) or 100.0, _num(work, "height", 100.0) or 100.0
    content = [c for c in list(work) if _local(c.tag) not in NOT_RENDERED]
    mask = ET.Element(f"{{{SVG_NS}}}mask", {"id": "mysuite-knockout", "maskUnits": "userSpaceOnUse",
                                             "x": f"{x - w:g}", "y": f"{y - h:g}", "width": f"{3 * w:g}", "height": f"{3 * h:g}"})
    ET.SubElement(mask, f"{{{SVG_NS}}}rect", {"x": f"{x - w:g}", "y": f"{y - h:g}", "width": f"{3 * w:g}",
                                              "height": f"{3 * h:g}", "fill": "#fff"})
    for c in content:
        clone = copy.deepcopy(c)
        for el in clone.iter():
            el.attrib.pop("id", None)
        mask.append(clone)
    mask_shapes = [s for s, _ in _painted(mask)][1:]          # [0] is the white rect
    mask_shapes[0].set("display", "none")                     # the background itself is not cut
    for s in mask_shapes[1:]:
        style = re.sub(r"(fill|stroke)\s*:[^;]*;?", "", s.get("style") or "")
        if style:
            s.set("style", style)
        else:
            s.attrib.pop("style", None)
        s.set("fill", "#000")
        if _strokes(s) or s.get("stroke"):
            s.set("stroke", "#000")
    defs = next((c for c in work if _local(c.tag) == "defs"), None)
    if defs is None:
        defs = ET.Element(f"{{{SVG_NS}}}defs")
        work.insert(0, defs)
    defs.append(mask)
    group = ET.Element(f"{{{SVG_NS}}}g", {"mask": "url(#mysuite-knockout)"})
    for c in content:
        work.remove(c)
        group.append(c)
    work.append(group)
    for s in shapes[1:]:
        s.set("display", "none")
    return ET.tostring(work, encoding="unicode")


def knockout(text: str) -> tuple[str, list[str]]:
    """Background shape with everything on it punched out. Returns (svg, notes)."""
    root, found, bg, fg = find_background(text)
    expected = bg & ~fg
    merged = _merged(root, found)
    if merged is not None:
        got = _alpha(merged) > 0.5
        if float((got != expected).mean()) <= MISMATCH_MAX:
            return merged, []
    masked = _masked(root)
    got = _alpha(masked) > 0.5
    notes = ["knockout uses an SVG mask (the shapes could not be merged into one path): PNG/SVG are exact; "
             "PDF/EPS keep it as a soft mask, so check print files"]
    diff = float((got != expected).mean())
    if diff > MISMATCH_MAX:
        notes.append(f"knockout differs from the expected shape in {diff:.1%} of pixels - check the result")
    return masked, notes


def cutout(text: str) -> tuple[str, list[str]]:
    """Everything except the background shape. Returns (svg, notes)."""
    root, found, _, _ = find_background(text)
    work = copy.deepcopy(root)
    first = _painted(work)[0][0]
    _parent_map(work)[first].remove(first)
    return ET.tostring(work, encoding="unicode"), []
