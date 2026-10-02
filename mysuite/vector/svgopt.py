"""SVG optimiser: smaller files that render the same. Pure Python, no extra dependency.

Only changes that cannot alter the picture are made:
  - comments, processing instructions, DOCTYPE, <metadata> and editor leftovers (Inkscape, Illustrator, Sketch, ...)
  - empty groups/defs, attribute-less groups are unwrapped, unreferenced <defs> entries and unreferenced ids dropped
  - colours written as rgb()/#rrggbb become the shortest hex (opaque colours only; names and hsl() are left alone)
  - numbers rounded to a precision (paths that use arcs are left exact: their flags are packed digit by digit)
  - default values that are not inherited (opacity="1", an unused root x/y of 0) and empty attributes

Anything that looks scripted or hostile (<script>, an ENTITY declaration) is refused or left alone. The command
verifies the result by rendering both versions and refuses to write a file that differs (see `verify`).
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from mysuite.color.svg import map_colours

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
_EDITOR_NS = (
    "http://www.inkscape.org/namespaces/inkscape", "http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd",
    "http://ns.adobe.com/AdobeIllustrator/10.0/", "http://ns.adobe.com/AdobeSVGViewerExtensions/3.0/",
    "http://ns.adobe.com/Extensibility/1.0/", "http://ns.adobe.com/Variables/1.0/", "http://ns.adobe.com/GenericCustomNamespace/1.0/",
    "http://ns.adobe.com/Flows/1.0/", "http://ns.adobe.com/ImageReplacement/1.0/", "http://ns.adobe.com/SaveForWeb/1.0/",
    "http://www.bohemiancoding.com/sketch/ns", "http://schema.org/", "http://purl.org/dc/elements/1.1/",
    "http://creativecommons.org/ns#", "http://www.w3.org/1999/02/22-rdf-syntax-ns#", "https://sketch.com/",
    "http://www.serif.com/", "http://ns.adobe.com/xap/1.0/", "http://www.figma.com/figma/ns",
)
_NUM_RE = re.compile(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?")
_DECIMAL_RE = re.compile(r"[-+]?(?:\d+\.\d*|\.\d+)(?:[eE][-+]?\d+)?")
_NUMERIC_ATTRS = {
    "d", "points", "transform", "gradientTransform", "patternTransform", "viewBox", "x", "y", "width", "height", "cx", "cy", "r",
    "rx", "ry", "x1", "y1", "x2", "y2", "fx", "fy", "offset", "stroke-width", "stroke-miterlimit", "stroke-dasharray",
    "stroke-dashoffset", "opacity", "fill-opacity", "stroke-opacity", "stop-opacity", "font-size",
}
_REF_RE = re.compile(r"url\(\s*['\"]?#([^)'\"\s]+)")
_STYLE_ID_RE = re.compile(r"#([A-Za-z_][\w.-]*)")


class OptimiseError(ValueError):
    pass


@dataclass
class OptimiseResult:
    text: str
    before: int
    after: int
    notes: list[str] = field(default_factory=list)

    @property
    def saved_percent(self) -> float:
        return round(100 * (1 - self.after / self.before), 1) if self.before else 0.0


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _ns(tag: str) -> str:
    return tag[1:].split("}", 1)[0] if isinstance(tag, str) and tag.startswith("{") else ""


def _format_number(token: str, precision: int) -> str:
    """Round a decimal token; integers are never touched."""
    if not _DECIMAL_RE.fullmatch(token):
        return token
    value = round(float(token), precision)
    if value == 0:
        return "0"
    text = f"{value:.{precision}f}".rstrip("0").rstrip(".")
    return text or "0"


def _round_numbers(value: str, precision: int) -> str:
    return _NUM_RE.sub(lambda m: _format_number(m.group(0), precision), value)


def _optimise_path(value: str, precision: int) -> str:
    if re.search(r"[aA]", value):          # arc flags may be packed without separators ("a1 1 0 011 1"): leave exact
        return " ".join(value.split())
    return " ".join(_round_numbers(value, precision).split())


def _short_colour(token: str, colour) -> str | None:
    lowered = token.lower()
    if colour.alpha is not None or not (lowered.startswith("rgb") or lowered.startswith("#")):
        return None
    r, g, b = colour.r, colour.g, colour.b
    if r >> 4 == r & 15 and g >> 4 == g & 15 and b >> 4 == b & 15:
        return f"#{r & 15:x}{g & 15:x}{b & 15:x}"
    return f"#{r:02x}{g:02x}{b:02x}"


def _collect_refs(root: ET.Element) -> set[str]:
    refs: set[str] = set()
    for el in root.iter():
        for name, value in el.attrib.items():
            if _local(name) == "href" and value.startswith("#"):
                refs.add(value[1:])
            for m in _REF_RE.finditer(value):
                refs.add(m.group(1))
            if _local(name) in ("begin", "end"):
                refs.update(re.findall(r"([A-Za-z_][\w.-]*)\.", value))
        if _local(el.tag) in ("style", "script") and el.text:
            refs.update(m.group(1) for m in _STYLE_ID_RE.finditer(el.text))
            refs.update(m.group(1) for m in _REF_RE.finditer(el.text))
    return refs


def optimise(text: str, *, precision: int = 3, keep_ids: bool = False, keep_title: bool = True) -> OptimiseResult:
    """Return the optimised SVG text. Raises OptimiseError for input that is not a plain SVG document."""
    if not 0 <= precision <= 8:
        raise OptimiseError("precision must be 0-8")
    if "<!ENTITY" in text:
        raise OptimiseError("the file declares XML entities; refusing to expand them")
    before = len(text.encode("utf-8"))
    notes: list[str] = []
    for prefix, uri in (("", SVG_NS), ("xlink", XLINK_NS)):
        ET.register_namespace(prefix, uri)
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise OptimiseError(f"not valid XML: {exc}") from exc
    if _local(root.tag) != "svg":
        raise OptimiseError("the root element is not <svg>")
    if any(_local(el.tag) == "script" for el in root.iter()):
        notes.append("contains <script>: ids and defs left alone")
        scripted = True
    else:
        scripted = False

    # --- editor leftovers: foreign-namespace attributes and elements, <metadata>, empty text
    def is_editor(name: str) -> bool:
        return _ns(name) in _EDITOR_NS

    for el in root.iter():
        for name in [n for n in el.attrib if is_editor(n)]:
            del el.attrib[name]
    for parent in list(root.iter()):
        for child in list(parent):
            local = _local(child.tag)
            if is_editor(child.tag) or local == "metadata" or (not keep_title and local in ("title", "desc")):
                parent.remove(child)

    # --- numbers, colours, defaults
    vb = root.attrib.get("viewBox", "").replace(",", " ").split()
    try:
        extent = max(abs(float(vb[2])), abs(float(vb[3]))) if len(vb) == 4 else None
    except ValueError:
        extent = None
    effective = precision + (2 if extent is not None and extent <= 20 else 0)
    for el in root.iter():
        for name, value in list(el.attrib.items()):
            local = _local(name)
            if local in _NUMERIC_ATTRS:
                digits = effective + 3 if local.endswith("ransform") else effective     # matrices need finer rounding
                value = _optimise_path(value, digits) if local == "d" else _round_numbers(value, digits)
                value = " ".join(value.split())
            if local in ("opacity", "fill-opacity", "stroke-opacity", "stop-opacity") and value in ("1", "1.0"):
                if local == "opacity":
                    del el.attrib[name]
                    continue
            if local == "style":
                value = ";".join(p.strip() for p in value.split(";") if p.strip())
            if value == "" and local in ("class", "style", "id"):
                del el.attrib[name]
                continue
            if local in ("fill", "stroke", "stop-color", "flood-color", "lighting-color", "color", "style"):
                probe = f'{local}="{value}"' if local != "style" else f'style="{value}"'
                mapped = map_colours(probe, _short_colour)
                if mapped != probe:
                    value = mapped[len(local) + 2:-1]
            el.attrib[name] = value
        if _local(el.tag) == "style" and el.text:
            el.text = map_colours(el.text, _short_colour)
    if _local(root.tag) == "svg":
        for attr in ("x", "y"):
            if root.attrib.get(attr) in ("0", "0px", "0.0"):
                del root.attrib[attr]
        for attr in ("version", "enable-background", "baseProfile"):
            root.attrib.pop(attr, None)

    # --- structure: empty and attribute-less groups, unreferenced defs and ids
    def prune(parent: ET.Element) -> None:
        index = 0
        while index < len(parent):
            child = parent[index]
            prune(child)
            local = _local(child.tag)
            empty = len(child) == 0 and not (child.text or "").strip() and not child.attrib.get("id")
            if local in ("g", "defs") and empty:
                parent.remove(child)
                continue
            if local == "g" and not child.attrib and not (child.text or "").strip():
                tail = child.tail
                children = list(child)
                parent.remove(child)
                for offset, grandchild in enumerate(children):
                    parent.insert(index + offset, grandchild)
                if children and tail:
                    children[-1].tail = (children[-1].tail or "") + tail
                continue
            index += 1

    prune(root)
    if not scripted and not keep_ids:
        refs = _collect_refs(root)
        for defs in [e for e in root.iter() if _local(e.tag) == "defs"]:
            for child in list(defs):
                cid = child.attrib.get("id")
                if cid and cid not in refs and _local(child.tag) not in ("style",):
                    defs.remove(child)
        refs = _collect_refs(root)
        css_selectors = any("[id" in (e.text or "") for e in root.iter() if _local(e.tag) == "style")
        if not css_selectors:
            for el in root.iter():
                if el.attrib.get("id") and el.attrib["id"] not in refs:
                    del el.attrib["id"]
        prune(root)

    # --- whitespace between elements is never significant outside <text>/<style>/<title>
    for el in root.iter():
        if _local(el.tag) not in ("text", "tspan", "style", "title", "desc", "textPath", "script"):
            if el.text is not None and not el.text.strip():
                el.text = None
        if el.tail is not None and not el.tail.strip() and _local(el.tag) not in ("tspan", "textPath"):
            el.tail = None

    out = ET.tostring(root, encoding="unicode", short_empty_elements=True)
    out = re.sub(r"\s+/>", "/>", out)
    return OptimiseResult(text=out, before=before, after=len(out.encode("utf-8")), notes=notes)
