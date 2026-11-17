"""PDF toolbox operations (Acrobat-style one-shot jobs). Pure pypdf for page work, Ghostscript for rendering and
compression, rsvg-convert for stamp/number overlays. Every operation writes new files; inputs are never modified."""
from __future__ import annotations

import base64
import io
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps
from pypdf import PageObject, PdfReader, PdfWriter, Transformation
from pypdf.errors import DependencyError, PdfReadError

from mysuite.config import ToolPaths
from mysuite.utils.subprocess_utils import atomic_write_via, run

MAX_PAGES = 5000

PAGE_SIZES_MM = {
    "a0": (841, 1189), "a1": (594, 841), "a2": (420, 594), "a3": (297, 420), "a4": (210, 297), "a5": (148, 210),
    "a6": (105, 148), "b5": (176, 250), "letter": (215.9, 279.4), "legal": (215.9, 355.6), "tabloid": (279.4, 431.8),
}
POSITIONS = {
    "bottom-center": (0.5, 1.0), "bottom-left": (0.0, 1.0), "bottom-right": (1.0, 1.0),
    "top-center": (0.5, 0.0), "top-left": (0.0, 0.0), "top-right": (1.0, 0.0), "center": (0.5, 0.5),
}


class PdfError(ValueError):
    pass


@dataclass
class OutFile:
    path: Path
    status: str = "written"            # written | skipped_existing | planned | not_smaller (compress: nothing written)
    pages: int | None = None
    note: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------- helpers
def open_pdf(path: Path) -> PdfReader:
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            # Owner-password-only PDFs ("permissions" PDFs) open with an empty user password, like in any viewer.
            # One that needs a password to open is refused: mysuite never asks for or guesses passwords.
            try:
                opened = reader.decrypt("")
            except DependencyError as exc:
                raise PdfError(f"{Path(path).name} is encrypted with AES; opening it needs the 'cryptography' Python package") from exc
            if not opened:
                raise PdfError(f"{Path(path).name} needs a password to open; mysuite does not open password-protected PDFs")
        count = len(reader.pages)
    except PdfError:
        raise
    except (PdfReadError, OSError, ValueError, KeyError, TypeError) as exc:
        raise PdfError(f"can't read {Path(path).name} as a PDF: {exc}") from exc
    if count == 0:
        raise PdfError(f"{Path(path).name} has no pages")
    if count > MAX_PAGES:
        raise PdfError(f"{Path(path).name} has {count} pages; the limit is {MAX_PAGES}")
    return reader


def parse_pages(spec: str, count: int) -> list[int]:
    """'1,3-5,8-' / 'last' / 'odd' / 'even' / 'all' -> zero-based page indexes, in the order written."""
    spec = spec.strip().lower()
    if spec in ("", "all"):
        return list(range(count))
    if spec == "odd":
        return list(range(0, count, 2))
    if spec == "even":
        return list(range(1, count, 2))
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            raise PdfError(f"empty page range in {spec!r}")
        if part == "last":
            out.append(count - 1)
            continue
        m = re.fullmatch(r"(\d+)?(-)?(\d+)?", part)
        if not m or not (m.group(1) or m.group(3)):
            raise PdfError(f"bad page range {part!r}: use numbers like 3, 3-5, 7- (to the end), last, odd, even")
        a, dash, b = m.groups()
        if not dash:
            start = end = int(a)
        else:
            start = int(a) if a else 1
            end = int(b) if b else count
        if start < 1 or end < 1 or start > end:
            raise PdfError(f"bad page range {part!r}")
        if end > count:
            raise PdfError(f"page {end} does not exist (the PDF has {count} pages)")
        out += list(range(start - 1, end))
    return out


def pages_label(idx: list[int]) -> str:
    """A file-name label for a page selection, by the pages it holds: [0] -> p1, [1, 2] -> p2-3, [2, 0] -> p3_1.
    Two different selections never share a name, so a second run with other pages is not silently skipped."""
    runs: list[list[int]] = []
    for i in idx:
        if runs and i == runs[-1][-1] + 1:
            runs[-1].append(i)
        else:
            runs.append([i])
    label = "p" + "_".join(f"{r[0] + 1}" if len(r) == 1 else f"{r[0] + 1}-{r[-1] + 1}" for r in runs)
    if len(label) <= 40:
        return label
    import hashlib

    digest = hashlib.sha1(",".join(map(str, idx)).encode()).hexdigest()[:6]
    return f"p{min(idx) + 1}-{max(idx) + 1}_{len(idx)}pages_{digest}"


def parse_page_size(text: str) -> tuple[float, float]:
    """'a4', 'letter', '210x297mm', '8.5x11in', '595x842' (points) -> (width, height) in PDF points."""
    t = text.strip().lower()
    if t in PAGE_SIZES_MM:
        w, h = PAGE_SIZES_MM[t]
        return w / 25.4 * 72, h / 25.4 * 72
    m = re.fullmatch(r"(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)(mm|cm|in|pt)?", t)
    if not m:
        raise PdfError(f"page size must be a name ({', '.join(PAGE_SIZES_MM)}) or like 210x297mm, 8.5x11in, 595x842: got {text!r}")
    unit = {"mm": 72 / 25.4, "cm": 72 / 2.54, "in": 72.0, "pt": 1.0, None: 1.0}[m.group(3)]
    w, h = float(m.group(1)) * unit, float(m.group(2)) * unit
    if w < 10 or h < 10:
        raise PdfError("page size is too small")
    return w, h


def _write(path: Path, writer: PdfWriter, *, strip_metadata: bool = False) -> None:
    def write(tmp: Path) -> None:
        if strip_metadata:
            writer.add_metadata({})
        with open(tmp, "wb") as fh:
            writer.write(fh)

    atomic_write_via(path, write, preserve_extension=True)


def _plan_or_skip(path: Path, overwrite: bool, dry_run: bool = False) -> OutFile | None:
    """skipped_existing for a file that is there (and no --overwrite); planned in a dry run; None means: write it."""
    if path.exists() and not overwrite:
        return OutFile(path, "skipped_existing")
    if dry_run:
        return OutFile(path, "planned")
    return None


def _same_file(a: Path, b: Path) -> bool:
    a, b = Path(os.path.abspath(a)), Path(os.path.abspath(b))
    if a == b:
        return True
    try:
        return a.exists() and b.exists() and os.path.samefile(a, b)     # also a.pdf vs A.pdf, links, ../
    except OSError:
        return False


def _refuse_inputs(target: Path, inputs: list[Path]) -> None:
    """Originals are never modified: an output that is one of the inputs is an error, even with --overwrite."""
    for src in inputs:
        if _same_file(target, src):
            raise PdfError(f"the output {target.name} is one of the input files; choose another --out")


def _suffix_path(src: Path, suffix: str, out: Path | None) -> Path:
    if out is not None:
        out = Path(os.path.abspath(out))
        target = out / f"{src.stem}{suffix}.pdf" if out.suffix.lower() != ".pdf" else out
    else:
        target = src.with_name(f"{src.stem}{suffix}.pdf")
    _refuse_inputs(target, [src])
    return target


def _need(tool: str, what: str, label: str = "Ghostscript (gs)") -> str:
    """The tool's resolved path, or a PdfError naming what needs it. Checked up front, so a FileNotFoundError later
    (a vanished temp file, a bad name) is never misreported as "tool missing"."""
    found = shutil.which(tool)
    if not found:
        raise PdfError(f"{what} needs {label} installed")
    return found


def _gs_out(path: Path) -> str:
    """-sOutputFile=… treats %d/%s as page placeholders; a literal % in a file name must be written %%."""
    return str(path).replace("%", "%%")


def _gs_run(gs: str, args: list[str], src: Path, tmp: Path) -> None:
    run([gs, "-q", "-dNOPAUSE", "-dBATCH", "-dSAFER", *args, f"-sOutputFile={_gs_out(tmp)}", str(Path(os.path.abspath(src)))])


# ---------------------------------------------------------------------------- read only
def info(path: Path) -> dict[str, Any]:
    reader = open_pdf(path)
    pages = reader.pages
    sizes = []
    for p in pages[:200]:
        w, h = float(p.mediabox.width), float(p.mediabox.height)
        sizes.append([round(w, 1), round(h, 1)])
    meta = {k.lstrip("/"): str(v) for k, v in (reader.metadata or {}).items()}
    return {
        "path": str(Path(os.path.abspath(path))), "pages": len(pages), "page_sizes_points": sizes[:5],
        "all_pages_same_size": len({tuple(s) for s in sizes}) == 1,
        "rotations": sorted({int(p.get("/Rotate", 0) or 0) for p in pages[:200]}),
        "metadata": meta, "has_metadata": bool(meta), "has_xmp": reader.xmp_metadata is not None,
        "encrypted": "owner password only" if reader.is_encrypted else False,
        "has_forms": bool(reader.get_fields()), "version": reader.pdf_header.lstrip("%PDF-"),
        "bytes": Path(path).stat().st_size,
        "preflight": preflight(reader),
    }


# ---------------------------------------------------------------------------- preflight (read only)
def _cs_name(cs: Any) -> str:
    cs = cs.get_object() if hasattr(cs, "get_object") else cs
    if isinstance(cs, list) and cs:
        kind = str(cs[0]).lstrip("/")
        if kind == "ICCBased":
            n = cs[1].get_object().get("/N") if hasattr(cs[1], "get_object") else (cs[1] or {}).get("/N")
            return f"ICCBased({ {1: 'Gray', 3: 'RGB', 4: 'CMYK'}.get(int(n), n) if n is not None else '?' })"
        if kind == "Separation":
            return f"Separation({str(cs[1]).lstrip('/')})"
        if kind == "Indexed":
            return f"Indexed({_cs_name(cs[1])})"
        return kind
    return str(cs).lstrip("/")


def _font_facts(font: Any) -> tuple[str, bool]:
    font = font.get_object()
    name = str(font.get("/BaseFont", "/(unnamed)")).lstrip("/")
    if font.get("/Subtype") == "/Type3":
        return name, True                                     # drawn by its own glyph procedures
    if font.get("/Subtype") == "/Type0":
        font = font["/DescendantFonts"][0].get_object()
    descriptor = font.get("/FontDescriptor")
    embedded = descriptor is not None and any(k in descriptor.get_object() for k in ("/FontFile", "/FontFile2", "/FontFile3"))
    return name, embedded


def preflight(reader: PdfReader, max_pages: int = 200) -> dict[str, Any]:
    """Print-check facts, read only: fonts (embedded or not), colour spaces, image count and the lowest effective image
    resolution (pixels per inch as placed on the page), trim/bleed boxes, and PDF/X or PDF/A claims. The first
    `max_pages` pages are looked at."""
    from pypdf.generic import ContentStream

    fonts: dict[str, bool] = {}
    spaces: set[str] = set()
    images = 0
    lowest: float | None = None
    seen: set[int] = set()

    def walk(content_owner: Any, resources: Any, ctm: list[float], depth: int) -> None:
        nonlocal images, lowest
        resources = resources.get_object() if resources is not None else {}
        for f in (resources.get("/Font") or {}).values():
            name, embedded = _font_facts(f)
            fonts[name] = fonts.get(name, False) or embedded
        for cs in (resources.get("/ColorSpace") or {}).values():
            spaces.add(_cs_name(cs))
        xobjects = {k: v for k, v in (resources.get("/XObject") or {}).items()}
        try:
            contents = content_owner.get_contents() if hasattr(content_owner, "get_contents") else content_owner
            ops_list = ContentStream(contents, reader).operations if contents is not None else []
        except Exception:  # noqa: BLE001 - an odd content stream must not break `pdf info`
            return
        stack: list[list[float]] = []
        m = list(ctm)
        for operands, op in ops_list:
            if op == b"q":
                stack.append(list(m))
            elif op == b"Q" and stack:
                m = stack.pop()
            elif op == b"cm" and len(operands) == 6:
                a, b, c, d, e, f = (float(x) for x in operands)
                m = [a * m[0] + b * m[2], a * m[1] + b * m[3], c * m[0] + d * m[2], c * m[1] + d * m[3],
                     e * m[0] + f * m[2] + m[4], e * m[1] + f * m[3] + m[5]]
            elif op in (b"rg", b"RG"):
                spaces.add("DeviceRGB")
            elif op in (b"k", b"K"):
                spaces.add("DeviceCMYK")
            elif op in (b"g", b"G"):
                spaces.add("DeviceGray")
            elif op == b"Do" and operands and operands[0] in xobjects:
                ref = xobjects[operands[0]]
                xo = ref.get_object()
                if xo.get("/Subtype") == "/Image":
                    images += 1
                    if "/ColorSpace" in xo:
                        spaces.add(_cs_name(xo["/ColorSpace"]))
                    placed_w = (m[0] ** 2 + m[1] ** 2) ** 0.5 / 72
                    placed_h = (m[2] ** 2 + m[3] ** 2) ** 0.5 / 72
                    if placed_w > 0 and placed_h > 0:
                        ppi = min(float(xo["/Width"]) / placed_w, float(xo["/Height"]) / placed_h)
                        lowest = ppi if lowest is None else min(lowest, ppi)
                elif xo.get("/Subtype") == "/Form" and depth < 8:
                    key = getattr(ref, "idnum", id(xo))
                    if (key, tuple(round(v, 3) for v in m)) in seen:
                        continue
                    seen.add((key, tuple(round(v, 3) for v in m)))
                    fm = [float(v) for v in xo.get("/Matrix", [1, 0, 0, 1, 0, 0])]
                    walk(xo, xo.get("/Resources") or resources, [
                        fm[0] * m[0] + fm[1] * m[2], fm[0] * m[1] + fm[1] * m[3], fm[2] * m[0] + fm[3] * m[2],
                        fm[2] * m[1] + fm[3] * m[3], fm[4] * m[0] + fm[5] * m[2] + m[4], fm[4] * m[1] + fm[5] * m[3] + m[5]],
                        depth + 1)

    pages = reader.pages[:max_pages]
    for page in pages:
        walk(page, page.get("/Resources"), [1.0, 0.0, 0.0, 1.0, 0.0, 0.0], 0)
    root = reader.trailer["/Root"]
    standard = None
    intents = [i.get_object() for i in (root.get("/OutputIntents") or [])]
    xmp = b""
    if "/Metadata" in root:
        try:
            xmp = root["/Metadata"].get_object().get_data()
        except Exception:  # noqa: BLE001
            xmp = b""
    xmp_x = re.search(rb"GTS_PDFXVersion(?:>|=\")\s*([^<\"]+)", xmp)
    info_x = (reader.metadata or {}).get("/GTS_PDFXVersion")
    ma = re.search(rb"pdfaid:part(?:>|=\")\s*(\d)", xmp)
    conf = re.search(rb"pdfaid:conformance(?:>|=\")\s*([A-Za-z])", xmp)
    if xmp_x or info_x:
        standard = (xmp_x.group(1).decode(errors="replace") if xmp_x else str(info_x)).strip()
    elif any(i.get("/S") == "/GTS_PDFX" for i in intents):
        standard = "PDF/X (output intent)"
    elif ma:
        standard = f"PDF/A-{ma.group(1).decode()}{conf.group(1).decode().lower() if conf else ''}"
    return {
        "pages_checked": len(pages),
        "fonts": sorted(fonts), "fonts_not_embedded": sorted(n for n, e in fonts.items() if not e),
        "colour_spaces": sorted(spaces), "images": images,
        "lowest_image_ppi": round(lowest) if lowest is not None else None,
        "trim_box": any("/TrimBox" in p for p in pages), "bleed_box": any("/BleedBox" in p for p in pages),
        "standard": standard,
        "output_intent": next((str(i.get("/OutputConditionIdentifier")) for i in intents if i.get("/OutputConditionIdentifier")), None),
    }


# ---------------------------------------------------------------------------- page jobs
def merge(paths: list[Path], out: Path | None, overwrite: bool, *, dry_run: bool = False) -> OutFile:
    if len(paths) < 2:
        raise PdfError("merge needs at least two PDFs")
    target = _suffix_path(Path(os.path.abspath(paths[0])), "_merged", out)
    _refuse_inputs(target, paths)
    if (skip := _plan_or_skip(target, overwrite, dry_run)):
        return skip
    writer = PdfWriter()
    for p in paths:
        reader = open_pdf(p)
        for page in reader.pages:
            writer.add_page(page)
    _write(target, writer)
    return OutFile(target, pages=len(writer.pages))


def extract(path: Path, pages: str, out: Path | None, overwrite: bool, *, dry_run: bool = False) -> OutFile:
    reader = open_pdf(path)
    idx = parse_pages(pages, len(reader.pages))
    if not idx:
        raise PdfError("no pages selected")
    target = _suffix_path(Path(os.path.abspath(path)), f"_{pages_label(idx)}", out)
    if (skip := _plan_or_skip(target, overwrite, dry_run)):
        return skip
    writer = PdfWriter()
    for i in idx:
        writer.add_page(reader.pages[i])
    _write(target, writer)
    return OutFile(target, pages=len(idx))


def split(path: Path, every: int | None, ranges: str | None, out_dir: Path | None, overwrite: bool, *, dry_run: bool = False) -> list[OutFile]:
    reader = open_pdf(path)
    count = len(reader.pages)
    if (every is None) == (ranges is None):
        raise PdfError("split needs exactly one of --every N or --ranges '1-3,4-6'")
    groups: list[tuple[str, list[int]]] = []
    if every is not None:
        if every < 1:
            raise PdfError("--every must be at least 1")
        width = len(str(count))
        for start in range(0, count, every):
            idx = list(range(start, min(start + every, count)))
            label = f"p{start + 1:0{width}d}" if every == 1 else f"p{idx[0] + 1:0{width}d}-{idx[-1] + 1:0{width}d}"
            groups.append((label, idx))
    else:
        for part in ranges.split(","):
            idx = parse_pages(part, count)
            groups.append((pages_label(idx), idx))
    folder = Path(os.path.abspath(out_dir)) if out_dir else Path(os.path.abspath(path)).parent
    results = []
    for label, idx in groups:
        target = folder / f"{Path(path).stem}_{label}.pdf"
        if (skip := _plan_or_skip(target, overwrite, dry_run)):
            results.append(skip)
            continue
        writer = PdfWriter()
        for i in idx:
            writer.add_page(reader.pages[i])
        _write(target, writer)
        results.append(OutFile(target, pages=len(idx)))
    return results


def rotate(path: Path, degrees: int, pages: str, out: Path | None, overwrite: bool, *, dry_run: bool = False) -> OutFile:
    if degrees % 90 != 0 or degrees == 0:
        raise PdfError("degrees must be a non-zero multiple of 90 (clockwise)")
    reader = open_pdf(path)
    chosen = set(parse_pages(pages, len(reader.pages)))
    target = _suffix_path(Path(os.path.abspath(path)), "_rotated", out)
    if (skip := _plan_or_skip(target, overwrite, dry_run)):
        return skip
    writer = PdfWriter()
    for i, page in enumerate(reader.pages):
        writer.add_page(page)
        if i in chosen:
            writer.pages[-1].rotate(degrees % 360)
    _write(target, writer)
    return OutFile(target, pages=len(reader.pages), extra={"rotated_pages": len(chosen)})


def resize(path: Path, size: str, out: Path | None, overwrite: bool, *, dry_run: bool = False) -> OutFile:
    tw, th = parse_page_size(size)
    reader = open_pdf(path)
    target = _suffix_path(Path(os.path.abspath(path)), "_resized", out)
    if (skip := _plan_or_skip(target, overwrite, dry_run)):
        return skip
    writer = PdfWriter()
    for page in reader.pages:
        w, h = float(page.mediabox.width), float(page.mediabox.height)
        if page.get("/Rotate", 0):
            page = page.transfer_rotation_to_content()
            w, h = float(page.mediabox.width), float(page.mediabox.height)
        # landscape source onto a portrait page size (and vice versa): turn the target, not the content
        pw, ph = (th, tw) if (w > h) != (tw > th) else (tw, th)
        scale = min(pw / w, ph / h)
        blank = PageObject.create_blank_page(width=pw, height=ph)
        tx, ty = (pw - w * scale) / 2 - float(page.mediabox.left) * scale, (ph - h * scale) / 2 - float(page.mediabox.bottom) * scale
        blank.merge_transformed_page(page, Transformation().scale(scale, scale).translate(tx, ty))
        writer.add_page(blank)
    _write(target, writer)
    return OutFile(target, pages=len(reader.pages))


def strip_metadata(path: Path, out: Path | None, overwrite: bool, *, dry_run: bool = False) -> OutFile:
    """Page content only: no /Info (title, author, producer, dates), no XMP packet, no annotation authors."""
    reader = open_pdf(path)
    target = _suffix_path(Path(os.path.abspath(path)), "_stripped", out)
    if (skip := _plan_or_skip(target, overwrite, dry_run)):
        return skip
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    for key in ("/Metadata", "/PieceInfo", "/OpenAction"):
        if key in writer._root_object:
            del writer._root_object[key]
    for page in writer.pages:
        for key in ("/Metadata", "/PieceInfo"):
            if key in page:
                del page[key]
        for ref in page.get("/Annots") or []:
            annot = ref.get_object()
            # /T on a markup annotation (note, highlight, ...) is its author; on a form field it is the field's
            # name, which the form needs, so widgets keep it.
            if annot.get("/Subtype") != "/Widget" and "/T" in annot:
                del annot["/T"]
            for key in ("/M", "/CreationDate"):                 # when it was written says when you worked on it
                if key in annot:
                    del annot[key]
    # placed artwork (images and forms from Photoshop, Illustrator, InDesign) carries its own XMP packet
    for obj in list(writer._objects):
        if obj is not None and hasattr(obj, "get") and obj.get("/Subtype") in ("/Image", "/Form"):
            for key in ("/Metadata", "/PieceInfo"):
                if key in obj:
                    del obj[key]
    writer.metadata = None
    writer.compress_identical_objects(remove_duplicates=False, remove_unreferenced=True)   # the detached XMP streams too          # no /Info in the trailer at all (pypdf would otherwise add its /Producer)

    def write(tmp: Path) -> None:
        with open(tmp, "wb") as fh:
            writer.write(fh)

    atomic_write_via(target, write, preserve_extension=True)
    return OutFile(target, pages=len(reader.pages))


# ---------------------------------------------------------------------------- SVG logos (kept as vectors)
SVG_SUFFIXES = (".svg", ".svgz")
_SVG_NS = "http://www.w3.org/2000/svg"


def is_svg(path: Path) -> bool:
    return Path(path).suffix.lower() in SVG_SUFFIXES


def _svg_number(value: str | None) -> float | None:
    m = re.fullmatch(r"\s*([0-9.]+)\s*(px|pt|mm|cm|in)?\s*", value or "")
    if not m:
        return None
    return float(m.group(1)) * {"px": 1.0, None: 1.0, "pt": 96 / 72, "mm": 96 / 25.4, "cm": 96 / 2.54, "in": 96.0}[m.group(2)]


class SvgLogo:
    """An SVG file nested inside the overlay SVG as an <svg> element, so rsvg-convert writes it as vectors (an <image>
    with an SVG data URI would be rasterised)."""

    def __init__(self, path: Path):
        import gzip
        import xml.etree.ElementTree as ET

        raw = Path(path).read_bytes()
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            raise PdfError(f"can't read {Path(path).name} as SVG: {exc}") from exc
        if root.tag != f"{{{_SVG_NS}}}svg":
            raise PdfError(f"{Path(path).name} is not an SVG file")
        vb = (root.get("viewBox") or "").replace(",", " ").split()
        if len(vb) == 4:
            vw, vh = float(vb[2]), float(vb[3])
        else:
            vw, vh = _svg_number(root.get("width")) or 0.0, _svg_number(root.get("height")) or 0.0
            root.set("viewBox", f"0 0 {vw} {vh}")
        if vw <= 0 or vh <= 0:
            raise PdfError(f"{Path(path).name} has no size (no viewBox and no width/height)")
        self.ratio = vh / vw
        self.width_px, self.height_px = (_svg_number(root.get("width")) or vw), (_svg_number(root.get("height")) or vh)
        self._root = root

    def element(self, x: float, y: float, w: float, h: float) -> str:
        import xml.etree.ElementTree as ET

        ET.register_namespace("", _SVG_NS)
        ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")
        for k, v in (("x", x), ("y", y), ("width", w), ("height", h)):
            self._root.set(k, f"{v:.3f}")
        self._root.set("preserveAspectRatio", "xMidYMid meet")
        return ET.tostring(self._root, encoding="unicode")


# ---------------------------------------------------------------------------- overlays (number / stamp)
def _overlay_pdf(width: float, height: float, body: str, tools: ToolPaths) -> bytes:
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
           f'width="{width}pt" height="{height}pt" viewBox="0 0 {width} {height}">{body}</svg>')
    with tempfile.TemporaryDirectory(prefix="mysuite-pdf-") as d:
        src, dst = Path(d) / "o.svg", Path(d) / "o.pdf"
        src.write_text(svg, encoding="utf-8")
        run([_need(tools.rsvg_convert, "page numbers and stamps", "rsvg-convert"), "--format=pdf", f"--output={dst}", str(src)])
        return dst.read_bytes()


# Helvetica-Bold advance widths in 1/1000 em (the PDF core font metrics); unknown characters count as 0.72 em.
_HELV_BOLD = dict(zip(
    " !\"#$%&'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_`abcdefghijklmnopqrstuvwxyz{|}~",
    [278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556, 556, 556, 556,
     556, 556, 333, 333, 584, 584, 584, 611, 975, 722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833, 722, 778,
     667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 333, 278, 333, 584, 556, 333, 556, 611, 556, 611, 556, 333, 611,
     611, 278, 278, 556, 278, 889, 611, 611, 611, 611, 389, 556, 333, 611, 556, 778, 556, 556, 500, 389, 280, 389, 584]))


def _text_width(text: str, size: float) -> float:
    """Width of a bold Helvetica/Arial line in points (Arial shares Helvetica's metrics). A few percent extra covers
    the substitute font rsvg-convert may pick."""
    return sum(_HELV_BOLD.get(ch, 720) for ch in text) / 1000 * size * 1.06


def _place(boxes: list[tuple[float, float, float, float]], angle: float, gx: float, gy: float, w: float, h: float,
           margin: float) -> tuple[float, float]:
    """Where to put a group's origin so its ROTATED bounding box sits in the chosen corner/edge, `margin` points from it.
    Corner stamps used to be centred on a point 40 pt from the corner, so half of 'CONFIDENTIAL' fell off the page."""
    import math

    a = math.radians(-angle)
    cos, sin = math.cos(a), math.sin(a)
    xs, ys = [], []
    for left, top, right, bottom in boxes:
        for x, y in ((left, top), (right, top), (left, bottom), (right, bottom)):
            xs.append(x * cos - y * sin)
            ys.append(x * sin + y * cos)
    lo_x, hi_x, lo_y, hi_y = min(xs), max(xs), min(ys), max(ys)

    def axis(g: float, lo: float, hi: float, size: float) -> float:
        if g == 0.0:
            return margin - lo
        if g == 1.0:
            return size - margin - hi
        return size / 2 - (lo + hi) / 2

    return axis(gx, lo_x, hi_x, w), axis(gy, lo_y, hi_y, h)


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _apply_overlay(path: Path, suffix: str, out: Path | None, overwrite: bool, tools: ToolPaths, make_body, *, dry_run: bool = False) -> OutFile:
    reader = open_pdf(path)
    target = _suffix_path(Path(os.path.abspath(path)), suffix, out)
    if (skip := _plan_or_skip(target, overwrite, dry_run)):
        return skip
    writer = PdfWriter()
    cache: dict[tuple, bytes] = {}
    total = len(reader.pages)
    for i, page in enumerate(reader.pages):
        if page.get("/Rotate", 0):
            page = page.transfer_rotation_to_content()
        w, h = float(page.mediabox.width), float(page.mediabox.height)
        body = make_body(i, total, w, h)
        if body is None:
            writer.add_page(page)
            continue
        key = (round(w, 1), round(h, 1), body)
        if key not in cache:
            cache[key] = _overlay_pdf(w, h, body, tools)
        overlay = PdfReader(io.BytesIO(cache[key])).pages[0]
        writer.add_page(page)
        # the overlay is drawn from (0, 0); a page whose MediaBox starts elsewhere needs it moved there
        left, bottom = float(page.mediabox.left), float(page.mediabox.bottom)
        if left or bottom:
            writer.pages[-1].merge_transformed_page(overlay, Transformation().translate(left, bottom))
        else:
            writer.pages[-1].merge_page(overlay)
    _write(target, writer)
    return OutFile(target, pages=total)


def number_pages(path: Path, out: Path | None, overwrite: bool, tools: ToolPaths, *, position: str = "bottom-center",
                 fmt: str = "{n}", start: int = 1, size: float = 10.0, color: str = "#333333", margin: float = 28.0,
                 pages: str = "all", dry_run: bool = False) -> OutFile:
    if position not in POSITIONS:
        raise PdfError(f"unknown position {position!r} - expected one of {', '.join(POSITIONS)}")
    if not 4 <= size <= 72:
        raise PdfError("size must be between 4 and 72 points")
    if "{n}" not in fmt and "{total}" not in fmt:
        raise PdfError("format must contain {n} (and may contain {total})")
    try:
        from PIL import ImageColor

        rgb = ImageColor.getrgb(color)[:3]
    except ValueError as exc:
        raise PdfError(f"unknown colour {color!r}: use #rrggbb or a colour name") from exc
    count = len(open_pdf(path).pages)
    chosen = set(parse_pages(pages, count))
    gx, gy = POSITIONS[position]

    def label_for(i: int, total: int) -> str:
        return fmt.replace("{n}", str(i + start)).replace("{total}", str(total - 1 + start if start != 1 else total))

    def baseline(h: float) -> float:          # from the top, like SVG
        return margin + size if gy == 0.0 else (h - margin if gy == 1.0 else h / 2 + size / 3)

    if dry_run:
        return _number_fast(path, out, overwrite, chosen, label_for, baseline, gx, size, rgb, margin, None, dry_run)
    text = "".join({label_for(i, count) for i in chosen})
    glyphs = _Glyphs.render(text, tools, color)
    if glyphs is not None:
        return _number_fast(path, out, overwrite, chosen, label_for, baseline, gx, size, rgb, margin, glyphs, dry_run)
    # a font rsvg-convert could only embed as a composite font (e.g. CJK): one overlay per page, slower

    def body(i: int, total: int, w: float, h: int) -> str | None:
        if i not in chosen:
            return None
        x = margin + gx * (w - 2 * margin)
        anchor = {0.0: "start", 0.5: "middle", 1.0: "end"}[gx]
        return (f'<text x="{x}" y="{baseline(h)}" font-family="Helvetica, Arial, sans-serif" font-size="{size}" '
                f'fill="{_esc(color)}" text-anchor="{anchor}">{_esc(label_for(i, total))}</text>')

    return _apply_overlay(path, "_numbered", out, overwrite, tools, body, dry_run=dry_run)


class _Glyphs:
    """Every character the page labels use, drawn ONCE by rsvg-convert in Helvetica/Arial. The embedded (subset) font that
    cairo writes is then reused for every page's label, with its exact widths: a print preflight sees embedded fonts, and
    4,000 pages need one rsvg-convert call instead of 4,000."""

    def __init__(self, fonts: dict[str, Any], codes: dict[str, tuple[str, bytes]]):
        self.fonts = fonts            # cairo's font name -> font dictionary (in the rsvg output)
        self.codes = codes            # character -> (font name, the bytes that draw it)

    @classmethod
    def render(cls, text: str, tools: ToolPaths, color: str) -> "_Glyphs | None":
        from pypdf.generic import ArrayObject, ContentStream

        chars = [c for c in dict.fromkeys(text) if not c.isspace()]
        if not chars:
            return cls({}, {})
        body = "".join(f'<text x="0" y="{(n + 1) * 120}" font-family="Helvetica, Arial, sans-serif" font-size="100">'
                       f'{_esc(c)}</text>' for n, c in enumerate(chars))
        reader = PdfReader(io.BytesIO(_overlay_pdf(200, (len(chars) + 1) * 120, body, tools)))
        page = reader.pages[0]
        font_dict = (page.get("/Resources") or {}).get("/Font") or {}
        fonts, shown, current = {}, [], None
        for operands, op in ContentStream(page.get_contents(), reader).operations:
            if op == b"Tf":
                current = str(operands[0])
            elif op in (b"Tj", b"TJ"):
                parts = operands[0] if op == b"TJ" else [operands[0]]
                raw = b"".join(_raw_bytes(x) for x in (parts if isinstance(parts, (list, ArrayObject)) else [parts])
                               if not isinstance(x, (int, float)))
                shown.append((current, raw))
        if len(shown) != len(chars) or any(len(raw) != 1 for _, raw in shown):
            return None
        for name, _ in shown:
            font = font_dict[name].get_object()
            if font.get("/Subtype") not in ("/TrueType", "/Type1") or "/Widths" not in font:
                return None           # a composite font (two-byte codes): use the per-page overlay instead
            fonts[name] = font
        return cls(fonts, dict(zip(chars, shown)))

    def width(self, ch: str, size: float) -> float:
        if ch not in self.codes:
            return 0.278 * size       # a space (not drawn): Helvetica's space width
        name, raw = self.codes[ch]
        font = self.fonts[name]
        first, widths = int(font.get("/FirstChar", 0)), font["/Widths"]
        k = raw[0] - first
        return (float(widths[k]) if 0 <= k < len(widths) else 556.0) / 1000 * size


def _raw_bytes(obj: Any) -> bytes:
    if isinstance(obj, bytes):
        return bytes(obj)
    get = getattr(obj, "get_original_bytes", None)
    return get() if get else str(obj).encode("latin-1")


def _number_fast(path: Path, out: Path | None, overwrite: bool, chosen: set[int], label_for, baseline, gx: float,
                 size: float, rgb: tuple[int, int, int], margin: float, glyphs: "_Glyphs | None",
                 dry_run: bool = False) -> OutFile:
    """Page numbers written straight into each page's content with the embedded font from _Glyphs: no overlay page and
    no rsvg-convert call per page, so 4,000 pages take about a second instead of minutes."""
    from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject

    reader = open_pdf(path)
    target = _suffix_path(Path(os.path.abspath(path)), "_numbered", out)
    if (skip := _plan_or_skip(target, overwrite, dry_run)):
        return skip
    assert glyphs is not None
    writer = PdfWriter()
    names = {name: NameObject(f"/MysuiteNum{n}") for n, name in enumerate(glyphs.fonts)}
    refs = {name: writer._add_object(font.clone(writer)) for name, font in glyphs.fonts.items()}
    push = writer._add_object(DecodedStreamObject())
    push.get_object().set_data(b"q\n")
    colour = " ".join(f"{c / 255:.4f}" for c in rgb).encode()
    total = len(reader.pages)
    for i, page in enumerate(reader.pages):
        if page.get("/Rotate", 0):
            page = page.transfer_rotation_to_content()
        writer.add_page(page)
        if i not in chosen:
            continue
        wp = writer.pages[-1]
        box = wp.mediabox
        left, bottom, w, h = float(box.left), float(box.bottom), float(box.width), float(box.height)
        label = label_for(i, total)
        x = left + margin + gx * (w - 2 * margin) - gx * sum(glyphs.width(ch, size) for ch in label)
        y = bottom + h - baseline(h)
        draw = [b"\nQ q BT " + colour + b" rg"]
        for ch in label:
            if ch in glyphs.codes:
                name, raw = glyphs.codes[ch]
                draw.append(f" {names[name]} {size:g} Tf 1 0 0 1 {x:.2f} {y:.2f} Tm <{raw.hex()}> Tj".encode())
            x += glyphs.width(ch, size)
        draw.append(b" ET Q\n")
        stream = DecodedStreamObject()
        stream.set_data(b"".join(draw))
        # the page's own drawing runs inside q … Q, so whatever state it leaves cannot move or recolour the number
        contents = wp.get("/Contents")
        existing = [] if contents is None else (list(contents.get_object()) if isinstance(contents.get_object(), ArrayObject) else [contents])
        wp[NameObject("/Contents")] = ArrayObject([push, *existing, writer._add_object(stream)])
        res = wp.get("/Resources")
        res = DictionaryObject() if res is None else res.get_object()
        fonts = res.get("/Font")
        fonts = DictionaryObject() if fonts is None else fonts.get_object()
        for name, ref in refs.items():
            fonts[names[name]] = ref
        res[NameObject("/Font")] = fonts
        wp[NameObject("/Resources")] = res
    _write(target, writer)
    return OutFile(target, pages=total)


def stamp(path: Path, out: Path | None, overwrite: bool, tools: ToolPaths, *, text: str | None = None, logo: Path | None = None,
          position: str = "center", opacity: float = 0.25, angle: float = 0.0, size: float = 48.0, color: str = "#cc0000",
          scale: float = 20.0, pages: str = "all", dry_run: bool = False) -> OutFile:
    if not text and not logo:
        raise PdfError("stamp needs --text and/or --logo")
    if position not in POSITIONS:
        raise PdfError(f"unknown position {position!r} - expected one of {', '.join(POSITIONS)}")
    if not 0 < opacity <= 1:
        raise PdfError("opacity must be above 0 and at most 1")
    if not 4 <= size <= 400:
        raise PdfError("size must be between 4 and 400 points")
    if not 1 <= scale <= 100:
        raise PdfError("scale must be between 1 and 100 (percent of the page width)")
    logo_uri = ""
    logo_ratio = 1.0
    svg_logo: SvgLogo | None = None
    if logo and is_svg(logo):
        svg_logo = SvgLogo(logo)
        logo_ratio = svg_logo.ratio
    elif logo:
        try:
            with Image.open(logo) as im:
                im = im.convert("RGBA")
                logo_ratio = im.height / im.width
                buf = io.BytesIO()
                im.save(buf, format="PNG")
                logo_uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        except Exception as exc:  # noqa: BLE001
            raise PdfError(f"can't read logo {Path(logo).name}: {exc}") from exc
    count = len(open_pdf(path).pages)
    chosen = set(parse_pages(pages, count))
    gx, gy = POSITIONS[position]
    text_w = _text_width(text, size) if text else 0.0

    def body(i: int, total: int, w: float, h: float) -> str | None:
        if i not in chosen:
            return None
        parts = []
        boxes: list[tuple[float, float, float, float]] = []          # (left, top, right, bottom) before rotation
        has_logo = bool(logo_uri or svg_logo)
        lw = w * scale / 100 if has_logo else 0.0
        lh = lw * logo_ratio
        if svg_logo:
            parts.append(svg_logo.element(-lw / 2, -lh / 2, lw, lh))
        elif logo_uri:
            parts.append(f'<image x="{-lw / 2}" y="{-lh / 2}" width="{lw}" height="{lh}" '
                         f'xlink:href="{logo_uri}" href="{logo_uri}" preserveAspectRatio="xMidYMid meet"/>')
        if has_logo:
            boxes.append((-lw / 2, -lh / 2, lw / 2, lh / 2))
        if text:
            dy = lh / 2 + size if has_logo else size * 0.35                 # baseline; caps centred when there is no logo
            parts.append(f'<text x="0" y="{dy}" font-family="Helvetica, Arial, sans-serif" font-weight="bold" '
                         f'font-size="{size}" fill="{_esc(color)}" text-anchor="middle">{_esc(text)}</text>')
            boxes.append((-text_w / 2, dy - size * 0.75, text_w / 2, dy + size * 0.22))
        cx, cy = _place(boxes, angle, gx, gy, w, h, margin=24.0)
        return f'<g opacity="{opacity}" transform="translate({cx} {cy}) rotate({-angle})">{"".join(parts)}</g>'

    return _apply_overlay(path, "_stamped", out, overwrite, tools, body, dry_run=dry_run)


# ---------------------------------------------------------------------------- images in/out
def extract_images(path: Path, out_dir: Path | None, overwrite: bool, *, dry_run: bool = False) -> list[OutFile]:
    reader = open_pdf(path)
    folder = Path(os.path.abspath(out_dir)) if out_dir else Path(os.path.abspath(path)).parent
    results = []
    for pno, page in enumerate(reader.pages, start=1):
        try:
            images = list(page.images)
        except Exception as exc:  # noqa: BLE001 - odd image filters
            results.append(OutFile(folder / f"{Path(path).stem}_p{pno}", "failed", note=f"page {pno}: {exc}"))
            continue
        for n, image in enumerate(images, start=1):
            ext = Path(image.name).suffix or ".png"
            target = folder / f"{Path(path).stem}_p{pno}_img{n}{ext}"
            if (skip := _plan_or_skip(target, overwrite, dry_run)):
                results.append(skip)
                continue
            data = image.data

            def write(tmp: Path, data: bytes = data) -> None:
                tmp.write_bytes(data)

            atomic_write_via(target, write, preserve_extension=True)
            results.append(OutFile(target, extra={"page": pno}))
    if not results:
        results.append(OutFile(Path(os.path.abspath(path)), "no_images", pages=len(reader.pages),
                               note="no embedded pictures in this PDF (text and vector drawings are not pictures; "
                                    "use pdf render to turn pages into images)"))
    return results


def render(path: Path, out_dir: Path | None, overwrite: bool, tools: ToolPaths, *, dpi: int = 150, fmt: str = "png",
          pages: str = "all", password_free: bool = True, dry_run: bool = False) -> list[OutFile]:
    if not 10 <= dpi <= 1200:
        raise PdfError("dpi must be between 10 and 1200")
    fmt = "jpeg" if fmt == "jpg" else fmt
    if fmt not in ("png", "jpeg"):
        raise PdfError("format must be png, jpeg or jpg")
    reader = open_pdf(path)
    count = len(reader.pages)
    idx = parse_pages(pages, count)
    folder = Path(os.path.abspath(out_dir)) if out_dir else Path(os.path.abspath(path)).parent
    width = len(str(count))
    gs = "" if dry_run else _need(tools.gs, "rendering pages")
    device = "png16m" if fmt == "png" else "jpeg"
    ext = ".png" if fmt == "png" else ".jpg"
    results = []
    for i in idx:
        target = folder / f"{Path(path).stem}_p{i + 1:0{width}d}{ext}"
        if (skip := _plan_or_skip(target, overwrite, dry_run)):
            results.append(skip)
            continue

        def write(tmp: Path, page_no: int = i + 1) -> None:
            _gs_run(gs, [f"-sDEVICE={device}", f"-r{dpi}", "-dTextAlphaBits=4", "-dGraphicsAlphaBits=4",
                         f"-dFirstPage={page_no}", f"-dLastPage={page_no}"], path, tmp)

        atomic_write_via(target, write, preserve_extension=True)
        with Image.open(target) as im:
            results.append(OutFile(target, extra={"page": i + 1, "size": list(im.size)}))
    return results


def from_images(images: list[Path], out: Path | None, overwrite: bool, *, size: str | None = None, dpi: int = 300,
                margin_mm: float = 0.0, tools: ToolPaths | None = None, dry_run: bool = False) -> OutFile:
    """One page per image. Raster images are placed at `dpi`; SVG files stay vectors (rsvg-convert), at their artwork
    size or fitted on the paper size."""
    if not images:
        raise PdfError("from-images needs at least one image")
    tools = tools or ToolPaths()
    first = Path(os.path.abspath(images[0]))
    default_name = f"{first.stem}{'_images' if len(images) > 1 else ''}.pdf"
    if out is None:
        target = first.with_name(default_name)
    else:
        out = Path(os.path.abspath(out))
        target = out if out.suffix.lower() == ".pdf" else out / default_name
    if (skip := _plan_or_skip(target, overwrite, dry_run)):
        return skip
    paper_pt = parse_page_size(size) if size and size.lower() != "fit" else None
    pages: list[PageObject] = []
    for p in images:
        pages.append(_svg_page(p, paper_pt, margin_mm, tools) if is_svg(p) else _raster_page(p, paper_pt, dpi, margin_mm))
    writer = PdfWriter()
    for page in pages:
        writer.add_page(page)
    _write(target, writer)
    return OutFile(target, pages=len(pages))


def _turned(paper: tuple[float, float], w: float, h: float) -> tuple[float, float]:
    """The paper in the image's orientation (a landscape picture goes on a landscape sheet)."""
    pw, ph = paper
    return (pw, ph) if (w <= h) == (pw <= ph) else (ph, pw)


def _raster_page(p: Path, paper_pt: tuple[float, float] | None, dpi: int, margin_mm: float) -> PageObject:
    try:
        with Image.open(p) as im:
            im.seek(0)
            rgba = ImageOps.exif_transpose(im).convert("RGBA")       # a phone photo stored sideways is shown upright
    except Exception as exc:  # noqa: BLE001
        raise PdfError(f"can't read {Path(p).name}: {exc}") from exc
    flat = Image.new("RGB", rgba.size, "white")
    flat.paste(rgba, mask=rgba.getchannel("A"))
    if paper_pt:
        pw, ph = (round(v / 72 * dpi) for v in _turned(paper_pt, flat.width, flat.height))
        m = round(margin_mm / 25.4 * dpi)
        scale = min((pw - 2 * m) / flat.width, (ph - 2 * m) / flat.height)
        fitted = flat.resize((max(1, round(flat.width * scale)), max(1, round(flat.height * scale))), Image.Resampling.LANCZOS)
        sheet = Image.new("RGB", (pw, ph), "white")
        sheet.paste(fitted, ((pw - fitted.width) // 2, (ph - fitted.height) // 2))
        flat = sheet
    buf = io.BytesIO()
    if Path(p).suffix.lower() in (".jpg", ".jpeg"):
        flat.save(buf, format="PDF", resolution=float(dpi), quality=95, subsampling=0)    # a photo: JPEG, high quality
        return PdfReader(io.BytesIO(buf.getvalue())).pages[0]
    return _lossless_page(flat, dpi)


def _lossless_page(image: Image.Image, dpi: int) -> PageObject:
    """A page with the picture embedded losslessly (Flate). Pillow's PDF writer stores RGB as a q75 JPEG, which smears
    logos, screenshots and line art."""
    from pypdf.generic import DecodedStreamObject, NameObject, NumberObject

    w_pt, h_pt = image.width / dpi * 72, image.height / dpi * 72
    xobj = DecodedStreamObject()
    xobj.set_data(image.convert("RGB").tobytes())
    xobj.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"),
                 NameObject("/Width"): NumberObject(image.width), NameObject("/Height"): NumberObject(image.height),
                 NameObject("/ColorSpace"): NameObject("/DeviceRGB"), NameObject("/BitsPerComponent"): NumberObject(8)})
    writer = PdfWriter()
    page = writer.add_blank_page(width=w_pt, height=h_pt)
    ref = writer._add_object(xobj.flate_encode())
    from pypdf.generic import DictionaryObject

    page[NameObject("/Resources")] = DictionaryObject({NameObject("/XObject"): DictionaryObject({NameObject("/Im0"): ref})})
    content = DecodedStreamObject()
    content.set_data(f"q {w_pt:.4f} 0 0 {h_pt:.4f} 0 0 cm /Im0 Do Q".encode())
    page[NameObject("/Contents")] = writer._add_object(content)
    buf = io.BytesIO()
    writer.write(buf)
    return PdfReader(io.BytesIO(buf.getvalue())).pages[0]


def _svg_page(p: Path, paper_pt: tuple[float, float] | None, margin_mm: float, tools: ToolPaths) -> PageObject:
    SvgLogo(p)                                                        # a clear error for a broken SVG
    rsvg = _need(tools.rsvg_convert, "SVG pages", "rsvg-convert")
    with tempfile.TemporaryDirectory(prefix="mysuite-pdf-") as d:
        dst = Path(d) / "page.pdf"
        run([rsvg, "--format=pdf", f"--output={dst}", str(Path(os.path.abspath(p)))])
        art = PdfReader(io.BytesIO(dst.read_bytes())).pages[0]
    if not paper_pt:
        return art
    w, h = float(art.mediabox.width), float(art.mediabox.height)
    pw, ph = _turned(paper_pt, w, h)
    m = margin_mm / 25.4 * 72
    scale = min((pw - 2 * m) / w, (ph - 2 * m) / h)
    sheet = PageObject.create_blank_page(width=pw, height=ph)
    sheet.merge_transformed_page(art, Transformation().scale(scale, scale).translate((pw - w * scale) / 2, (ph - h * scale) / 2))
    return sheet


COMPRESS_LEVELS = ("screen", "ebook", "printer", "prepress")        # smallest first


def parse_byte_size(text: str) -> int:
    """'10MB', '500 KB', '1.5mb', '2000' (bytes) -> bytes. KB/MB are decimal (1 MB = 1,000,000 bytes), the stricter
    reading of an upload or e-mail limit."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(b|kb|k|mb|m)?\s*", (text or "").lower())
    if not m:
        raise PdfError(f"size must look like 10MB, 500KB or a number of bytes: got {text!r}")
    value = float(m.group(1)) * {None: 1, "b": 1, "k": 1e3, "kb": 1e3, "m": 1e6, "mb": 1e6}[m.group(2)]
    if value < 1:
        raise PdfError("size must be above 0")
    return int(value)


def _human(n: int) -> str:
    return f"{n / 1e6:.1f} MB" if n >= 1e6 else f"{max(1, round(n / 1e3))} KB"


def compress_to(path: Path, max_bytes: int, out: Path | None, overwrite: bool, tools: ToolPaths, *, dry_run: bool = False) -> OutFile:
    """The best-quality level whose result fits in max_bytes (prepress, printer, ebook, then screen). Nothing is
    written when even 'screen' is too big, or when the original already fits."""
    open_pdf(path)
    target = _suffix_path(Path(os.path.abspath(path)), "_compressed", out)
    before = Path(path).stat().st_size
    if before <= max_bytes:
        return OutFile(target, "not_smaller", note=f"not written: the original is already {_human(before)}, under {_human(max_bytes)}",
                       extra={"bytes_before": before, "max_bytes": max_bytes})
    gs = "" if dry_run else _need(tools.gs, "compressing a PDF")
    if (skip := _plan_or_skip(target, overwrite, dry_run)):
        skip.extra["max_bytes"] = max_bytes
        return skip
    tried: dict[str, int] = {}
    with tempfile.TemporaryDirectory(prefix="mysuite-pdf-") as d:
        for level in reversed(COMPRESS_LEVELS):
            trial = Path(d) / f"{level}.pdf"
            _gs_run(gs, ["-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.6", f"-dPDFSETTINGS=/{level}"], path, trial)
            tried[level] = trial.stat().st_size
            if tried[level] <= max_bytes:
                atomic_write_via(target, lambda tmp, src=trial: shutil.copyfile(src, tmp), preserve_extension=True)
                return OutFile(target, pages=len(PdfReader(str(target)).pages),
                               extra={"bytes_before": before, "bytes_after": tried[level], "level": level, "max_bytes": max_bytes})
    raise PdfError(f"can't get {Path(path).name} under {_human(max_bytes)}: the smallest result is {_human(tried['screen'])} "
                   f"(level screen, from {_human(before)}); nothing was written. Try fewer pages, or render pages as JPEG.")


def compress(path: Path, level: str, out: Path | None, overwrite: bool, tools: ToolPaths, *, dry_run: bool = False) -> OutFile:
    levels = {name: f"/{name}" for name in COMPRESS_LEVELS}
    if level not in levels:
        raise PdfError(f"level must be one of {', '.join(levels)}")
    open_pdf(path)
    target = _suffix_path(Path(os.path.abspath(path)), "_compressed", out)
    gs = "" if dry_run else _need(tools.gs, "compressing a PDF")
    if (skip := _plan_or_skip(target, overwrite, dry_run)):
        return skip

    before = Path(path).stat().st_size
    sizes: dict[str, int] = {}

    def write(tmp: Path) -> None:
        _gs_run(gs, ["-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.6", f"-dPDFSETTINGS={levels[level]}"], path, tmp)
        sizes["after"] = tmp.stat().st_size
        if sizes["after"] >= before:
            raise _NotSmaller                     # atomic_write_via removes the temp file; nothing is written

    try:
        atomic_write_via(target, write, preserve_extension=True)
    except _NotSmaller:
        return OutFile(target, "not_smaller", note=(
            f"not written: the result would not be smaller ({before} -> {sizes['after']} bytes at level {level}); "
            "the original is already compact"), extra={"bytes_before": before, "bytes_after": sizes["after"]})
    after = target.stat().st_size
    return OutFile(target, pages=len(PdfReader(str(target)).pages), extra={"bytes_before": before, "bytes_after": after})


class _NotSmaller(Exception):
    pass
