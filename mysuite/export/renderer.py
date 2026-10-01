from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from mysuite.color import pdfink
from mysuite.color.cmyk import CmykEngine, CmykSettings
from mysuite.config import ToolPaths
from mysuite.export.models import BundleJob, ExportJob, ExportPlan
from mysuite.export.planner import ICNS_STANDARD_PIXEL_SIZES
from mysuite.export.units import Margin, Size, resolve_margin
from mysuite.utils.subprocess_utils import atomic_write_via as _atomic_write_via
from mysuite.utils.subprocess_utils import run

_SIZE_ATTR_RE = re.compile(r'\b(width|height)\s*=\s*"(\d+(?:\.\d+)?)(?:px)?"')

# Standard Apple .iconset filenames for each of the fixed icns sizes. Some pixel
# sizes map to more than one filename (e.g. 32px is both icon_16x16@2x.png and
# icon_32x32.png) — rendered once, copied to every required name.
_ICNS_ICONSET_FILENAMES: dict[int, list[str]] = {
    16: ["icon_16x16.png"],
    32: ["icon_16x16@2x.png", "icon_32x32.png"],
    64: ["icon_32x32@2x.png"],
    128: ["icon_128x128.png"],
    256: ["icon_128x128@2x.png", "icon_256x256.png"],
    512: ["icon_256x256@2x.png", "icon_512x512.png"],
    1024: ["icon_512x512@2x.png"],
}


@dataclass
class ExecutionResult:
    written: list[Path] = field(default_factory=list)
    skipped_existing: list[Path] = field(default_factory=list)
    cmyk_conversions: list = field(default_factory=list)  # color.cmyk.Conversion, one per distinct flat colour
    cmyk_notes: list[str] = field(default_factory=list)


def native_svg_size(svg_path: Path) -> int | None:
    """Best-effort detection of a square SVG's native pixel size, for the
    resize-vs-copy shortcut. Returns None if it can't be determined cleanly."""
    try:
        text = svg_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    head = text[:2000]
    found: dict[str, float] = {}
    for match in _SIZE_ATTR_RE.finditer(head):
        found[match.group(1)] = float(match.group(2))
    if "width" in found and "height" in found and found["width"] == found["height"]:
        return int(found["width"])
    return None


def _rsvg_render_png(input_svg: Path, output_path: Path, size: Size, dpi: float, tools: ToolPaths) -> None:
    _rsvg_render_png_dims(input_svg, output_path, size.label, size.label, dpi, tools)


def _rsvg_render_png_dims(
    input_svg: Path, output_path: Path, width: str, height: str, dpi: float, tools: ToolPaths
) -> None:
    run([
        tools.rsvg_convert,
        "--format=png",
        f"--width={width}",
        f"--height={height}",
        f"--dpi-x={dpi}",
        f"--dpi-y={dpi}",
        "--keep-aspect-ratio",
        f"--output={output_path}",
        str(input_svg),
    ])


def _render_square_png(
    input_svg: Path, output_path: Path, *, request_label: str, target_px: int, dpi: float, tools: ToolPaths
) -> None:
    """Renders input_svg at request_label x request_label (rsvg-convert's own
    unit interpretation, e.g. a physical size like "5cm"), then unconditionally
    pads it to an exact target_px x target_px canvas via ImageMagick.
    rsvg-convert's --keep-aspect-ratio does NOT pad the canvas to the requested
    size for a non-square source — it shrinks the CANVAS ITSELF to match the
    content's own aspect ratio instead (e.g. a 1024x1024 request on a 426x156
    source produces a 1024x375 PNG). Icon bundle formats (ico/icns) require an
    exactly-square frame at every declared size, so this is used there instead
    of the raw rsvg-convert call; regular per-size exports keep using the raw
    call since a non-square output for a non-square source is normal there."""

    def write(tmp_path: Path) -> None:
        natural = tmp_path.with_suffix(".natural.png")
        try:
            _rsvg_render_png_dims(input_svg, natural, request_label, request_label, dpi, tools)
            run([
                tools.magick, str(natural),
                "-gravity", "center", "-background", "none",
                "-extent", f"{target_px}x{target_px}",
                str(tmp_path),
            ])
        finally:
            if natural.exists():
                natural.unlink()

    _atomic_write_via(output_path, write)


def _apply_background_and_margin(
    png_path: Path, *, background: str | None, margin: Margin, tools: ToolPaths
) -> None:
    """In-place ImageMagick pass: flatten onto a background color (if given) and/or
    add an asymmetric margin (if requested) via -splice, which — unlike -border —
    supports independent top/right/bottom/left amounts. No-op if neither is asked
    for. The margin itself is never forced onto a background: with no background
    color it stays transparent (safe space, not a colored box)."""
    ops: list[str] = []
    splice_color = background if background else "none"
    if background:
        ops += ["-background", background, "-flatten"]
    if margin.top > 0:
        ops += ["-gravity", "North", "-background", splice_color, "-splice", f"0x{margin.top}"]
    if margin.bottom > 0:
        ops += ["-gravity", "South", "-background", splice_color, "-splice", f"0x{margin.bottom}"]
    if margin.left > 0:
        ops += ["-gravity", "West", "-background", splice_color, "-splice", f"{margin.left}x0"]
    if margin.right > 0:
        ops += ["-gravity", "East", "-background", splice_color, "-splice", f"{margin.right}x0"]
    if ops:
        run([tools.magick, str(png_path), *ops, str(png_path)])


def _render_png(
    input_svg: Path,
    output_path: Path,
    size: Size,
    dpi: float,
    tools: ToolPaths,
    *,
    normalize_png: bool,
    background: str | None,
    margin: Margin,
    png_compression: int | None,
) -> None:
    def write(tmp_path: Path) -> None:
        _rsvg_render_png(input_svg, tmp_path, size, dpi, tools)
        _apply_background_and_margin(tmp_path, background=background, margin=margin, tools=tools)
        ops: list[str] = []
        if normalize_png:
            ops += ["-strip", "-colorspace", "sRGB"]
        if png_compression is not None:
            ops += ["-define", f"png:compression-level={png_compression}"]
        if ops:
            run([tools.magick, str(tmp_path), *ops, str(tmp_path)])

    _atomic_write_via(output_path, write)


def _render_derived_raster(
    input_svg: Path,
    output_path: Path,
    size: Size,
    dpi: float,
    tools: ToolPaths,
    *,
    magick_format_token: str,
    background: str | None,
    margin: Margin,
    extra_magick_args: list[str],
) -> None:
    """Shared path for formats ImageMagick derives from a base PNG: jpeg/webp/tiff."""

    def write(tmp_path: Path) -> None:
        base_png = tmp_path.with_suffix(".base.png")
        try:
            _rsvg_render_png(input_svg, base_png, size, dpi, tools)
            _apply_background_and_margin(base_png, background=background, margin=margin, tools=tools)
            run([tools.magick, str(base_png), *extra_magick_args, f"{magick_format_token}:{tmp_path}"])
        finally:
            if base_png.exists():
                base_png.unlink()

    _atomic_write_via(output_path, write)


def _render_cmyk_tiff(
    input_svg: Path, output_path: Path, size: Size, dpi: float, tools: ToolPaths, engine: CmykEngine,
    *, background: str | None, margin: Margin, notes: list[str],
) -> None:
    """CMYK TIFF through the same colour engine as the PDF (was ImageMagick's own, different, conversion)."""
    from PIL import Image

    def write(tmp_path: Path) -> None:
        base_png = tmp_path.with_suffix(".base.png")
        try:
            _rsvg_render_png(input_svg, base_png, size, dpi, tools)
            _apply_background_and_margin(base_png, background=background, margin=margin, tools=tools)
            with Image.open(base_png) as im:
                im = im.convert("RGBA")
                if im.getchannel("A").getextrema()[0] < 255:
                    note = "CMYK TIFF has no transparency: transparent areas were flattened on white (use --background to choose)"
                    if note not in notes:
                        notes.append(note)
                flat = Image.new("RGB", im.size, "white")
                flat.paste(im, mask=im.getchannel("A"))
                cmyk = engine.convert_image(flat)
            cmyk.save(tmp_path, format="TIFF", compression="tiff_lzw", dpi=(dpi, dpi), icc_profile=engine.icc_bytes)
        finally:
            if base_png.exists():
                base_png.unlink()

    _atomic_write_via(output_path, write, preserve_extension=True)


def _render_pdf_rgb(input_svg: Path, output_path: Path, size: Size, dpi: float, tools: ToolPaths) -> None:
    def write(tmp_path: Path) -> None:
        run([
            tools.rsvg_convert,
            "--format=pdf",
            f"--width={size.label}",
            f"--height={size.label}",
            f"--dpi-x={dpi}",
            f"--dpi-y={dpi}",
            "--keep-aspect-ratio",
            f"--output={tmp_path}",
            str(input_svg),
        ])

    _atomic_write_via(output_path, write)


def _gs_pdf(tools: ToolPaths, input_pdf: Path, output_path: Path, *extra: str) -> None:
    run([
        tools.gs, "-q", "-dBATCH", "-dNOPAUSE", "-dSAFER", "-sDEVICE=pdfwrite",
        "-dCompatibilityLevel=1.4", *extra, f"-sOutputFile={output_path}", str(input_pdf),
    ])


def _build_cmyk_pdf(
    rgb_pdf: Path, output_path: Path, tools: ToolPaths, engine: CmykEngine
) -> int:
    """RGB master -> CMYK PDF whose flat colours carry exactly the engine's numbers.
    Returns how many non-flat RGB constructs (gradients, images, groups) Ghostscript had to convert itself."""
    def write(tmp_path: Path) -> None:
        classic = tmp_path.with_suffix(".classic.pdf")
        rewritten = tmp_path.with_suffix(".k.pdf")
        try:
            # 1. classic-structure RGB copy (cairo writes object streams we don't edit)
            _gs_pdf(tools, rgb_pdf, classic, "-sColorConversionStrategy=LeaveColorUnchanged")
            # 2. flat rg/RG/g/G -> k/K with our numbers
            data = pdfink.rewrite_flat_colours(classic.read_bytes(), engine.convert01)
            remaining[0] = pdfink.count_rgb_operators(data)
            rewritten.write_bytes(data)
            # 3. Ghostscript converts whatever is still RGB (gradients, images); k/K stay as written
            _gs_pdf(
                tools, rewritten, tmp_path,
                "-sColorConversionStrategy=CMYK", "-dProcessColorModel=/DeviceCMYK",
                f"--permit-file-read={engine.profile_path}",   # -dSAFER blocks reading a user-chosen profile otherwise
                f"-sOutputICCProfile={engine.profile_path}",
            )
            # 4. say which CMYK the numbers mean
            tmp_path.write_bytes(pdfink.add_output_intent(tmp_path.read_bytes(), engine.icc_bytes, engine.description))
        finally:
            for leftover in (classic, rewritten):
                leftover.unlink(missing_ok=True)

    remaining = [0]
    _atomic_write_via(output_path, write)
    return remaining[0]


def _convert_pdf_to_eps(input_pdf: Path, output_path: Path, tools: ToolPaths) -> None:
    def write(tmp_path: Path) -> None:
        run([
            tools.gs,
            "-dBATCH",
            "-dNOPAUSE",
            "-dSAFER",
            "-sDEVICE=eps2write",
            "-dCompatibilityLevel=1.4",
            f"-sOutputFile={tmp_path}",
            str(input_pdf),
        ])

    _atomic_write_via(output_path, write)


def _render_svg(input_svg: Path, output_path: Path, size: Size, dpi: float, tools: ToolPaths) -> None:
    native = native_svg_size(input_svg)
    if native == size.pixels:
        def write(tmp_path: Path) -> None:
            shutil.copyfile(input_svg, tmp_path)
    else:
        def write(tmp_path: Path) -> None:
            run([
                tools.rsvg_convert,
                "--format=svg",
                f"--width={size.label}",
                f"--height={size.label}",
                f"--dpi-x={dpi}",
                f"--dpi-y={dpi}",
                "--keep-aspect-ratio",
                f"--output={tmp_path}",
                str(input_svg),
            ])

    _atomic_write_via(output_path, write)


def _render_ico_bundle(
    input_svg: Path,
    bundle_job: BundleJob,
    dpi: float,
    tools: ToolPaths,
    *,
    background: str | None,
    margin_top: str | None,
    margin_right: str | None,
    margin_bottom: str | None,
    margin_left: str | None,
) -> None:
    def write(tmp_path: Path) -> None:
        frame_paths: list[Path] = []
        try:
            for i, size in enumerate(sorted(bundle_job.sizes, key=lambda s: s.pixels)):
                frame_path = tmp_path.with_suffix(f".frame{i}.png")
                _render_square_png(
                    input_svg, frame_path, request_label=size.label, target_px=size.pixels,
                    dpi=dpi, tools=tools,
                )
                margin = resolve_margin(
                    margin_top, margin_right, margin_bottom, margin_left, reference_px=size.pixels
                )
                _apply_background_and_margin(
                    frame_path, background=background, margin=margin, tools=tools
                )
                frame_paths.append(frame_path)
            run([tools.magick, *[str(p) for p in frame_paths], f"ico:{tmp_path}"])
        finally:
            for p in frame_paths:
                if p.exists():
                    p.unlink()

    _atomic_write_via(bundle_job.output_path, write)


def _render_icns_bundle(
    input_svg: Path,
    bundle_job: BundleJob,
    dpi: float,
    tools: ToolPaths,
    *,
    background: str | None,
    margin_top: str | None,
    margin_right: str | None,
    margin_bottom: str | None,
    margin_left: str | None,
) -> None:
    output_path = bundle_job.output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # iconutil refuses any --output path that doesn't literally end in ".icns",
    # so the usual "<name>.<ext>.tmp" atomic-write scheme doesn't work here —
    # use a sibling "<name>.tmp.icns" instead, then rename into place.
    tmp_icns_path = output_path.with_name(output_path.stem + ".tmp.icns")
    iconset_dir = output_path.with_name(output_path.stem + ".tmp.iconset")
    if iconset_dir.exists():
        shutil.rmtree(iconset_dir)
    iconset_dir.mkdir(parents=True)
    # iconutil enforces two strict rules mysuite's usual "grow the canvas" margin
    # would violate: (1) each filename's declared size must match its file's
    # actual pixel dimensions exactly — icon_16x16.png must BE 16x16, not
    # 16px-of-art-plus-margin — and (2) every "@2x" file must be EXACTLY double
    # its 1x pair. So for icns, margin instead SHRINKS the rendered artwork
    # within the fixed canvas (render smaller, then splice back out to the
    # exact required size), and each side's base margin is computed once at the
    # smallest icns size and scaled by the same power-of-2 factor as the pixel
    # size, which keeps every doubling relationship exact by construction.
    base_pixels = min(ICNS_STANDARD_PIXEL_SIZES)
    base_margin = resolve_margin(
        margin_top, margin_right, margin_bottom, margin_left, reference_px=base_pixels
    )
    try:
        for size in bundle_job.sizes:
            filenames = _ICNS_ICONSET_FILENAMES.get(size.pixels, [])
            if not filenames:
                continue
            rendered = iconset_dir / filenames[0]
            factor = size.pixels // base_pixels
            margin = Margin(
                top=base_margin.top * factor,
                right=base_margin.right * factor,
                bottom=base_margin.bottom * factor,
                left=base_margin.left * factor,
            )
            content_w = max(size.pixels - margin.left - margin.right, 1)
            content_h = max(size.pixels - margin.top - margin.bottom, 1)
            # rsvg-convert's --keep-aspect-ratio shrinks-to-fit rather than
            # padding when width != height, so a non-square content box (from
            # asymmetric left/right vs top/bottom margins) can't be rendered
            # directly — render the square artwork at the limiting dimension,
            # then center-pad transparently to the exact content box first.
            content_square = min(content_w, content_h)
            _render_square_png(
                input_svg, rendered, request_label=str(content_square), target_px=content_square,
                dpi=dpi, tools=tools,
            )
            if content_w != content_h:
                run([
                    tools.magick, str(rendered),
                    "-gravity", "center", "-background", "none",
                    "-extent", f"{content_w}x{content_h}",
                    str(rendered),
                ])
            _apply_background_and_margin(
                rendered, background=background, margin=margin, tools=tools
            )
            for extra_name in filenames[1:]:
                shutil.copyfile(rendered, iconset_dir / extra_name)
        run([tools.iconutil, "-c", "icns", "-o", str(tmp_icns_path), str(iconset_dir)])
        os.replace(tmp_icns_path, output_path)
    finally:
        shutil.rmtree(iconset_dir, ignore_errors=True)
        if tmp_icns_path.exists():
            tmp_icns_path.unlink()


class Renderer:
    def __init__(
        self,
        tools: ToolPaths,
        *,
        dpi: float = 300,
        normalize_png: bool = True,
        overwrite: bool = False,
        background: str | None = None,
        quality: int | None = None,
        margin_top: str | None = None,
        margin_right: str | None = None,
        margin_bottom: str | None = None,
        margin_left: str | None = None,
        png_compression: int | None = None,
        cmyk: CmykSettings | None = None,
    ):
        self.cmyk = cmyk or CmykSettings()
        self._engine: CmykEngine | None = None
        self.tools = tools
        self.dpi = dpi
        self.normalize_png = normalize_png
        self.overwrite = overwrite
        self.background = background
        self.quality = quality
        self.margin_top = margin_top
        self.margin_right = margin_right
        self.margin_bottom = margin_bottom
        self.margin_left = margin_left
        self.png_compression = png_compression

    def _cmyk_engine(self) -> CmykEngine:
        if self._engine is None:
            self._engine = CmykEngine(self.cmyk, gs_binary=self.tools.gs)
        return self._engine

    def _margin(self, reference_px: int) -> Margin:
        return resolve_margin(
            self.margin_top, self.margin_right, self.margin_bottom, self.margin_left,
            reference_px=reference_px,
        )

    def execute(self, plan: ExportPlan, input_svg: Path, *, on_job_done=None) -> ExecutionResult:
        result = ExecutionResult()

        by_size: dict[Size, list[ExportJob]] = {}
        for job in plan.jobs:
            by_size.setdefault(job.size, []).append(job)

        for size, jobs in by_size.items():
            pdf_master_cache: dict[str, Path] = {}  # colorspace -> rendered pdf master (always a private temp file)

            def get_pdf_master(colorspace: str, size=size) -> Path:
                if colorspace in pdf_master_cache:
                    return pdf_master_cache[colorspace]
                # Always rendered fresh into a private temp file — an on-disk PDF
                # left over from an earlier run (e.g. before a --recolor change)
                # can't be trusted to reflect the current input_svg, so EPS/CMYK
                # derivation never reuses what already sits at a "pdf" job's own
                # output_path, even when overwrite is off and that file exists.
                target = input_svg.parent / f".mysuite-tmp-{input_svg.stem}-{size.label}-{colorspace}.pdf"
                if colorspace == "rgb":
                    _render_pdf_rgb(input_svg, target, size, self.dpi, self.tools)
                else:
                    rgb_master = get_pdf_master("rgb")
                    unflat = _build_cmyk_pdf(rgb_master, target, self.tools, self._cmyk_engine())
                    if unflat:
                        note = (f"{unflat} gradient/image/transparency colour space(s) were converted by "
                                "Ghostscript with the same profile, not rewritten to the chosen mode")
                        if note not in result.cmyk_notes:
                            result.cmyk_notes.append(note)
                pdf_master_cache[colorspace] = target
                return target

            margin = self._margin(size.pixels)

            # Render PDFs first so EPS jobs can reuse the master.
            for job in sorted(jobs, key=lambda j: j.format != "pdf"):
                if not self.overwrite and job.output_path.exists():
                    result.skipped_existing.append(job.output_path)
                    if on_job_done:
                        on_job_done(job, skipped=True)
                    continue

                if job.format == "png":
                    _render_png(
                        input_svg, job.output_path, size, self.dpi, self.tools,
                        normalize_png=self.normalize_png,
                        background=self.background,
                        margin=margin,
                        png_compression=self.png_compression,
                    )
                elif job.format == "jpeg":
                    _render_derived_raster(
                        input_svg, job.output_path, size, self.dpi, self.tools,
                        magick_format_token="jpg",
                        background=self.background or "white",
                        margin=margin,
                        extra_magick_args=["-quality", str(self.quality)] if self.quality is not None else [],
                    )
                elif job.format == "webp":
                    _render_derived_raster(
                        input_svg, job.output_path, size, self.dpi, self.tools,
                        magick_format_token="webp",
                        background=self.background,
                        margin=margin,
                        extra_magick_args=["-quality", str(self.quality)] if self.quality is not None else [],
                    )
                elif job.format == "tiff" and job.colorspace == "cmyk":
                    _render_cmyk_tiff(
                        input_svg, job.output_path, size, self.dpi, self.tools, self._cmyk_engine(),
                        background=self.background, margin=margin, notes=result.cmyk_notes,
                    )
                elif job.format == "tiff":
                    extra: list[str] = []
                    _render_derived_raster(
                        input_svg, job.output_path, size, self.dpi, self.tools,
                        magick_format_token="tiff",
                        background=self.background,
                        margin=margin,
                        extra_magick_args=extra,
                    )
                elif job.format == "pdf":
                    master = get_pdf_master(job.colorspace)
                    _atomic_write_via(job.output_path, lambda tmp, master=master: shutil.copyfile(master, tmp))
                elif job.format == "eps":
                    pdf_master = get_pdf_master(job.colorspace)
                    _convert_pdf_to_eps(pdf_master, job.output_path, self.tools)
                elif job.format == "svg":
                    _render_svg(input_svg, job.output_path, size, self.dpi, self.tools)
                else:
                    raise ValueError(f"unsupported format: {job.format}")

                result.written.append(job.output_path)
                if on_job_done:
                    on_job_done(job, skipped=False)

            # Every cached master is a private temp file now (never a job's own
            # output_path), so it always needs cleaning up after this size's jobs.
            for path in pdf_master_cache.values():
                if path.exists():
                    path.unlink()

        if self._engine is not None:
            result.cmyk_conversions = self._engine.conversions
            result.cmyk_notes += self._engine.warnings()

        for bundle_job in plan.bundle_jobs:
            if not self.overwrite and bundle_job.output_path.exists():
                result.skipped_existing.append(bundle_job.output_path)
                if on_job_done:
                    on_job_done(bundle_job, skipped=True)
                continue

            if bundle_job.format == "ico":
                _render_ico_bundle(
                    input_svg, bundle_job, self.dpi, self.tools,
                    background=self.background,
                    margin_top=self.margin_top, margin_right=self.margin_right,
                    margin_bottom=self.margin_bottom, margin_left=self.margin_left,
                )
            elif bundle_job.format == "icns":
                _render_icns_bundle(
                    input_svg, bundle_job, self.dpi, self.tools,
                    background=self.background,
                    margin_top=self.margin_top, margin_right=self.margin_right,
                    margin_bottom=self.margin_bottom, margin_left=self.margin_left,
                )
            else:
                raise ValueError(f"unsupported bundle format: {bundle_job.format}")

            result.written.append(bundle_job.output_path)
            if on_job_done:
                on_job_done(bundle_job, skipped=False)

        return result
