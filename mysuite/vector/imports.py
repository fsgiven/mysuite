"""Bring PDF, Illustrator (.ai), EPS and SVGZ files in as plain SVG so recolor, tokens and variants can work on them.

PDF-compatible .ai files (everything saved by Illustrator since CS) are read as PDF; older PostScript-based .ai and
EPS go through Ghostscript to a PDF first. poppler's `pdftocairo` then writes the SVG. Text becomes outlines (glyph shapes),
so nothing depends on fonts being installed. Live effects, meshes and overprint are flattened by the PDF itself.
"""
from __future__ import annotations

import gzip
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from mysuite.config import ToolPaths
from mysuite.utils.subprocess_utils import MysuiteToolError, run

IMPORT_EXTENSIONS = frozenset({".pdf", ".ai", ".eps", ".svgz"})
MAX_UNZIPPED_BYTES = 100 * 1024 * 1024


class ImportError_(RuntimeError):
    pass


@dataclass
class Imported:
    svg: Path
    pages: int = 1
    notes: list[str] = field(default_factory=list)


def _kind(path: Path) -> str:
    head = path.read_bytes()[:1024]
    suffix = path.suffix.lower()
    if suffix == ".svgz" or head[:2] == b"\x1f\x8b":
        return "svgz"
    if b"%PDF-" in head:
        return "pdf"
    if head.startswith(b"%!PS") or head.startswith(b"\xc5\xd0\xd3\xc6"):
        return "ps"
    raise ImportError_(f"{path.name}: not a PDF, Illustrator, EPS or SVGZ file")


def page_count(pdf: Path) -> int:
    from pypdf import PdfReader

    try:
        return len(PdfReader(str(pdf)).pages)
    except Exception as exc:  # noqa: BLE001 - any parse failure is a bad input, not a crash
        raise ImportError_(f"{pdf.name}: cannot read it as a PDF ({exc})") from exc


def _bbox(pdf: Path, page: int, tools: ToolPaths) -> tuple[float, float, float, float] | None:
    proc = subprocess.run(
        [tools.gs, "-q", "-dNOPAUSE", "-dBATCH", "-sDEVICE=bbox", f"-dFirstPage={page}", f"-dLastPage={page}", str(pdf)],
        capture_output=True, text=True,
    )
    m = re.search(r"%%HiResBoundingBox:\s*([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)", proc.stderr)
    if not m:
        return None
    x0, y0, x1, y1 = (float(v) for v in m.groups())
    return (x0, y0, x1, y1) if x1 > x0 and y1 > y0 else None


def _crop_to_content(svg: Path, bbox: tuple[float, float, float, float], margin: float = 0.5) -> bool:
    """pdftocairo writes the whole page (viewBox = its size in points, y pointing down). Trim to the drawn content."""
    text = svg.read_text(encoding="utf-8")
    m = re.search(r'<svg\b[^>]*\bviewBox="0 0 ([\d.]+) ([\d.]+)"', text)
    if not m:
        return False
    page_w, page_h = float(m.group(1)), float(m.group(2))
    x0, y0, x1, y1 = bbox
    left, top = max(0.0, x0 - margin), max(0.0, page_h - y1 - margin)
    width, height = min(page_w, x1 + margin) - left, min(page_h, page_h - y0 + margin) - top
    if width <= 0 or height <= 0 or (width >= page_w - 1 and height >= page_h - 1):
        return False
    root = re.search(r"<svg\b[^>]*>", text)
    tag = root.group(0)
    tag = re.sub(r'\bwidth="[^"]*"', f'width="{width:g}pt"', tag, count=1)
    tag = re.sub(r'\bheight="[^"]*"', f'height="{height:g}pt"', tag, count=1)
    tag = re.sub(r'\bviewBox="[^"]*"', f'viewBox="{left:.3f} {top:.3f} {width:.3f} {height:.3f}"', tag, count=1)
    svg.write_text(text[:root.start()] + tag + text[root.end():], encoding="utf-8")
    return True


def to_svg(src: Path, dest_dir: Path, *, page: int = 1, tools: ToolPaths, crop: bool = True, name: str | None = None) -> Imported:
    """Write `<dest_dir>/<name or stem>.svg` from a PDF/AI/EPS/SVGZ file."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / f"{name or src.stem}.svg"
    kind = _kind(src)
    if kind == "svgz":
        with gzip.open(src, "rb") as handle:
            data = handle.read(MAX_UNZIPPED_BYTES + 1)
        if len(data) > MAX_UNZIPPED_BYTES:
            raise ImportError_(f"{src.name}: unpacks to more than {MAX_UNZIPPED_BYTES // 2**20} MB; refusing")
        out.write_bytes(data)
        return Imported(out)
    if shutil.which(tools.pdftocairo) is None:
        raise ImportError_("pdftocairo (poppler) is not installed: run `mysuite install tools --yes` or `brew install poppler`")
    notes: list[str] = []
    with tempfile.TemporaryDirectory(prefix="mysuite-import-") as tmp:
        pdf = src
        if kind == "ps":
            pdf = Path(tmp) / "in.pdf"
            try:
                run([tools.gs, "-q", "-dNOPAUSE", "-dBATCH", "-dSAFER", "-dEPSCrop", "-sDEVICE=pdfwrite", f"-sOutputFile={pdf}", str(src)])
            except MysuiteToolError as exc:
                raise ImportError_(f"{src.name}: Ghostscript could not read it ({(exc.stderr or '').strip().splitlines()[-1:] or ['unknown error']})") from exc
            notes.append("PostScript source converted through Ghostscript")
        pages = page_count(pdf)
        if not 1 <= page <= pages:
            raise ImportError_(f"{src.name}: has {pages} page(s); --page {page} does not exist")
        try:
            run([tools.pdftocairo, "-svg", "-f", str(page), "-l", str(page), str(pdf), str(out)])
        except MysuiteToolError as exc:
            raise ImportError_(f"{src.name}: pdftocairo failed ({(exc.stderr or '').strip().splitlines()[-1:] or ['unknown error']})") from exc
        if crop:
            box = _bbox(pdf, page, tools)
            if box and _crop_to_content(out, box):
                notes.append("trimmed to the drawn content (use --no-crop for the whole page)")
    if "<image" in out.read_text(encoding="utf-8", errors="ignore"):
        notes.append("contains embedded raster pictures: those stay pixels, not vectors")
    if pages > 1:
        notes.append(f"{pages} pages; imported page {page}")
    return Imported(out, pages, notes)
