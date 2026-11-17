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

from PIL import Image
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


def _plan_or_skip(path: Path, overwrite: bool) -> OutFile | None:
    if path.exists() and not overwrite:
        return OutFile(path, "skipped_existing")
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
    }


# ---------------------------------------------------------------------------- page jobs
def merge(paths: list[Path], out: Path | None, overwrite: bool) -> OutFile:
    if len(paths) < 2:
        raise PdfError("merge needs at least two PDFs")
    target = _suffix_path(Path(os.path.abspath(paths[0])), "_merged", out)
    _refuse_inputs(target, paths)
    if (skip := _plan_or_skip(target, overwrite)):
        return skip
    writer = PdfWriter()
    for p in paths:
        reader = open_pdf(p)
        for page in reader.pages:
            writer.add_page(page)
    _write(target, writer)
    return OutFile(target, pages=len(writer.pages))


def extract(path: Path, pages: str, out: Path | None, overwrite: bool) -> OutFile:
    reader = open_pdf(path)
    idx = parse_pages(pages, len(reader.pages))
    if not idx:
        raise PdfError("no pages selected")
    target = _suffix_path(Path(os.path.abspath(path)), f"_{pages_label(idx)}", out)
    if (skip := _plan_or_skip(target, overwrite)):
        return skip
    writer = PdfWriter()
    for i in idx:
        writer.add_page(reader.pages[i])
    _write(target, writer)
    return OutFile(target, pages=len(idx))


def split(path: Path, every: int | None, ranges: str | None, out_dir: Path | None, overwrite: bool) -> list[OutFile]:
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
        if (skip := _plan_or_skip(target, overwrite)):
            results.append(skip)
            continue
        writer = PdfWriter()
        for i in idx:
            writer.add_page(reader.pages[i])
        _write(target, writer)
        results.append(OutFile(target, pages=len(idx)))
    return results


def rotate(path: Path, degrees: int, pages: str, out: Path | None, overwrite: bool) -> OutFile:
    if degrees % 90 != 0 or degrees == 0:
        raise PdfError("degrees must be a non-zero multiple of 90 (clockwise)")
    reader = open_pdf(path)
    chosen = set(parse_pages(pages, len(reader.pages)))
    target = _suffix_path(Path(os.path.abspath(path)), "_rotated", out)
    if (skip := _plan_or_skip(target, overwrite)):
        return skip
    writer = PdfWriter()
    for i, page in enumerate(reader.pages):
        writer.add_page(page)
        if i in chosen:
            writer.pages[-1].rotate(degrees % 360)
    _write(target, writer)
    return OutFile(target, pages=len(reader.pages), extra={"rotated_pages": len(chosen)})


def resize(path: Path, size: str, out: Path | None, overwrite: bool) -> OutFile:
    tw, th = parse_page_size(size)
    reader = open_pdf(path)
    target = _suffix_path(Path(os.path.abspath(path)), "_resized", out)
    if (skip := _plan_or_skip(target, overwrite)):
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


def strip_metadata(path: Path, out: Path | None, overwrite: bool) -> OutFile:
    """Page content only: no /Info (title, author, producer, dates), no XMP packet, no annotation authors."""
    reader = open_pdf(path)
    target = _suffix_path(Path(os.path.abspath(path)), "_stripped", out)
    if (skip := _plan_or_skip(target, overwrite)):
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
    writer.metadata = None          # no /Info in the trailer at all (pypdf would otherwise add its /Producer)

    def write(tmp: Path) -> None:
        with open(tmp, "wb") as fh:
            writer.write(fh)

    atomic_write_via(target, write, preserve_extension=True)
    return OutFile(target, pages=len(reader.pages))


# ---------------------------------------------------------------------------- overlays (number / stamp)
def _overlay_pdf(width: float, height: float, body: str, tools: ToolPaths) -> bytes:
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
           f'width="{width}pt" height="{height}pt" viewBox="0 0 {width} {height}">{body}</svg>')
    with tempfile.TemporaryDirectory(prefix="mysuite-pdf-") as d:
        src, dst = Path(d) / "o.svg", Path(d) / "o.pdf"
        src.write_text(svg, encoding="utf-8")
        run([_need(tools.rsvg_convert, "page numbers and stamps", "rsvg-convert"), "--format=pdf", f"--output={dst}", str(src)])
        return dst.read_bytes()


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _apply_overlay(path: Path, suffix: str, out: Path | None, overwrite: bool, tools: ToolPaths, make_body) -> OutFile:
    reader = open_pdf(path)
    target = _suffix_path(Path(os.path.abspath(path)), suffix, out)
    if (skip := _plan_or_skip(target, overwrite)):
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
        writer.pages[-1].merge_page(overlay)
    _write(target, writer)
    return OutFile(target, pages=total)


def number_pages(path: Path, out: Path | None, overwrite: bool, tools: ToolPaths, *, position: str = "bottom-center",
                 fmt: str = "{n}", start: int = 1, size: float = 10.0, color: str = "#333333", margin: float = 28.0,
                 pages: str = "all") -> OutFile:
    if position not in POSITIONS:
        raise PdfError(f"unknown position {position!r} - expected one of {', '.join(POSITIONS)}")
    if not 4 <= size <= 72:
        raise PdfError("size must be between 4 and 72 points")
    if "{n}" not in fmt and "{total}" not in fmt:
        raise PdfError("format must contain {n} (and may contain {total})")
    count = len(open_pdf(path).pages)
    chosen = set(parse_pages(pages, count))
    gx, gy = POSITIONS[position]

    def body(i: int, total: int, w: float, h: int) -> str | None:
        if i not in chosen:
            return None
        label = fmt.replace("{n}", str(i + start)).replace("{total}", str(total - 1 + start if start != 1 else total))
        x = margin + gx * (w - 2 * margin)
        y = margin + size if gy == 0.0 else (h - margin if gy == 1.0 else h / 2 + size / 3)
        anchor = {0.0: "start", 0.5: "middle", 1.0: "end"}[gx]
        return (f'<text x="{x}" y="{y}" font-family="Helvetica, Arial, sans-serif" font-size="{size}" '
                f'fill="{_esc(color)}" text-anchor="{anchor}">{_esc(label)}</text>')

    return _apply_overlay(path, "_numbered", out, overwrite, tools, body)


def stamp(path: Path, out: Path | None, overwrite: bool, tools: ToolPaths, *, text: str | None = None, logo: Path | None = None,
          position: str = "center", opacity: float = 0.25, angle: float = 0.0, size: float = 48.0, color: str = "#cc0000",
          scale: float = 20.0, pages: str = "all") -> OutFile:
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
    if logo:
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

    def body(i: int, total: int, w: float, h: float) -> str | None:
        if i not in chosen:
            return None
        cx = 40 + gx * (w - 80)
        cy = 40 + gy * (h - 80)
        parts = []
        if logo_uri:
            lw = w * scale / 100
            parts.append(f'<image x="{-lw / 2}" y="{-lw * logo_ratio / 2}" width="{lw}" height="{lw * logo_ratio}" '
                         f'xlink:href="{logo_uri}" href="{logo_uri}"/>')
        if text:
            dy = (lw_h := (w * scale / 100 * logo_ratio / 2 + size) if logo_uri else 0)
            parts.append(f'<text x="0" y="{dy}" font-family="Helvetica, Arial, sans-serif" font-weight="bold" '
                         f'font-size="{size}" fill="{_esc(color)}" text-anchor="middle">{_esc(text)}</text>')
        return f'<g opacity="{opacity}" transform="translate({cx} {cy}) rotate({-angle})">{"".join(parts)}</g>'

    return _apply_overlay(path, "_stamped", out, overwrite, tools, body)


# ---------------------------------------------------------------------------- images in/out
def extract_images(path: Path, out_dir: Path | None, overwrite: bool) -> list[OutFile]:
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
            if (skip := _plan_or_skip(target, overwrite)):
                results.append(skip)
                continue
            data = image.data

            def write(tmp: Path, data: bytes = data) -> None:
                tmp.write_bytes(data)

            atomic_write_via(target, write, preserve_extension=True)
            results.append(OutFile(target, extra={"page": pno}))
    return results


def render(path: Path, out_dir: Path | None, overwrite: bool, tools: ToolPaths, *, dpi: int = 150, fmt: str = "png",
          pages: str = "all", password_free: bool = True) -> list[OutFile]:
    if not 10 <= dpi <= 1200:
        raise PdfError("dpi must be between 10 and 1200")
    if fmt not in ("png", "jpeg"):
        raise PdfError("format must be png or jpeg")
    reader = open_pdf(path)
    count = len(reader.pages)
    idx = parse_pages(pages, count)
    folder = Path(os.path.abspath(out_dir)) if out_dir else Path(os.path.abspath(path)).parent
    width = len(str(count))
    gs = _need(tools.gs, "rendering pages")
    device = "png16m" if fmt == "png" else "jpeg"
    ext = ".png" if fmt == "png" else ".jpg"
    results = []
    for i in idx:
        target = folder / f"{Path(path).stem}_p{i + 1:0{width}d}{ext}"
        if (skip := _plan_or_skip(target, overwrite)):
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
                margin_mm: float = 0.0) -> OutFile:
    if not images:
        raise PdfError("from-images needs at least one image")
    first = Path(os.path.abspath(images[0]))
    default_name = f"{first.stem}{'_images' if len(images) > 1 else ''}.pdf"
    if out is None:
        target = first.with_name(default_name)
    else:
        out = Path(os.path.abspath(out))
        target = out if out.suffix.lower() == ".pdf" else out / default_name
    if (skip := _plan_or_skip(target, overwrite)):
        return skip
    page_px = None
    if size and size.lower() != "fit":
        w_pt, h_pt = parse_page_size(size)
        page_px = (round(w_pt / 72 * dpi), round(h_pt / 72 * dpi))
    pages: list[Image.Image] = []
    for p in images:
        try:
            with Image.open(p) as im:
                im.seek(0)
                rgba = im.convert("RGBA")
        except Exception as exc:  # noqa: BLE001
            raise PdfError(f"can't read {Path(p).name}: {exc}") from exc
        flat = Image.new("RGB", rgba.size, "white")
        flat.paste(rgba, mask=rgba.getchannel("A"))
        if page_px:
            pw, ph = page_px if (flat.width <= flat.height) == (page_px[0] <= page_px[1]) else page_px[::-1]
            m = round(margin_mm / 25.4 * dpi)
            scale = min((pw - 2 * m) / flat.width, (ph - 2 * m) / flat.height)
            fitted = flat.resize((max(1, round(flat.width * scale)), max(1, round(flat.height * scale))), Image.Resampling.LANCZOS)
            sheet = Image.new("RGB", (pw, ph), "white")
            sheet.paste(fitted, ((pw - fitted.width) // 2, (ph - fitted.height) // 2))
            flat = sheet
        pages.append(flat)

    def write(tmp: Path) -> None:
        pages[0].save(tmp, format="PDF", save_all=True, append_images=pages[1:], resolution=float(dpi))

    atomic_write_via(target, write, preserve_extension=True)
    return OutFile(target, pages=len(pages))


def compress(path: Path, level: str, out: Path | None, overwrite: bool, tools: ToolPaths) -> OutFile:
    levels = {"screen": "/screen", "ebook": "/ebook", "printer": "/printer", "prepress": "/prepress"}
    if level not in levels:
        raise PdfError(f"level must be one of {', '.join(levels)}")
    open_pdf(path)
    gs = _need(tools.gs, "compressing a PDF")
    target = _suffix_path(Path(os.path.abspath(path)), "_compressed", out)
    if (skip := _plan_or_skip(target, overwrite)):
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
