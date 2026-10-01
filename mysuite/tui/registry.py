from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from textual.screen import Screen


@dataclass(frozen=True)
class ToolSpec:
    key: str
    label: str
    description: str
    screen_factory: Callable[[], Screen]
    # Each tool's identity color — shown on the Home list and as that tool's
    # screen's border-title color (see theme.css's "<ScreenClass> .field-group"
    # rules), so it's visually obvious which tool you're in at a glance. Draw
    # new colors from the same palette family as these two (bright, desaturated
    # pastels that read clearly on the app's near-black background) rather than
    # reusing an existing tool's color or the warning/error/success hues.
    accent: str = "#7DD3FC"
    # Shown by the helper (F1 / h): what the tool does, how to use its screen, and the same job in the terminal.
    help: str = ""
    cli: str = ""


def _export_screen_factory() -> Screen:
    from mysuite.tui.screens.export_screen import ExportScreen

    return ExportScreen()


def _convert_screen_factory() -> Screen:
    from mysuite.tui.screens.convert_screen import ConvertScreen

    return ConvertScreen()


def _cutout_screen_factory() -> Screen:
    from mysuite.tui.screens.cutout_screen import CutoutScreen

    return CutoutScreen()


def _watermark_screen_factory() -> Screen:
    from mysuite.tui.screens.watermark_screen import WatermarkScreen

    return WatermarkScreen()


def _metadata_screen_factory() -> Screen:
    from mysuite.tui.screens.metadata_screen import MetadataScreen

    return MetadataScreen()


def _enhance_screen_factory() -> Screen:
    from mysuite.tui.screens.enhance_screen import EnhanceScreen

    return EnhanceScreen()


def _compress_screen_factory() -> Screen:
    from mysuite.tui.screens.compress_screen import CompressScreen

    return CompressScreen()


TOOL_REGISTRY: list[ToolSpec] = [
    ToolSpec(
        key="export",
        label="Export",
        description="One SVG logo into many sizes, formats and colour profiles.",
        screen_factory=_export_screen_factory,
        accent="#7DD3FC",  # cyan
        help=(
            "Pick an SVG (or drop one in), choose sizes like 64, 512, 2cm, formats (PNG, PDF, EPS, SVG, JPEG, WebP, "
            "TIFF, ICO, ICNS) and RGB and/or CMYK. Ask for 500 and you get exactly 500 px wide.\n"
            "CMYK: 'exact' keeps the profile's numbers, 'clean' snaps them to multiples of 5. Greys are black ink only.\n"
            "Colour swaps: add a row (#dd0000 → #0057b8); near-identical colours are caught too.\n"
            "Nothing is overwritten unless you tick it; originals are never touched."
        ),
        cli="mysuite export logo.svg --formats png,pdf --sizes 64,512 --profiles rgb,cmyk --cmyk-mode clean",
    ),
    ToolSpec(
        key="convert",
        label="Convert",
        description="Change a file's format, in place beside the source.",
        screen_factory=_convert_screen_factory,
        accent="#C4B5FD",  # violet
        help=(
            "Choose files or a folder and one or more target formats. Results land next to the originals with the "
            "new extension.\nTransparent images become white behind JPEG (or pick a colour). A multi-page PDF or "
            "animated GIF keeps its first page/frame for single-image targets, and says so."
        ),
        cli="mysuite convert photo.png --to webp,jpeg",
    ),
    ToolSpec(
        key="cutout",
        label="Cutout",
        description="Isolate a photo's subject onto a transparent background.",
        screen_factory=_cutout_screen_factory,
        accent="#FCA5A5",  # coral
        help=(
            "macOS only: uses Apple's Vision model to find the main subject and writes name_cutout.png with a "
            "transparent background.\nIt can pick the wrong subject; if nothing is found it reports a failure "
            "instead of writing an empty file. GPS and serial numbers are removed from the result."
        ),
        cli="mysuite cutout photo.jpg",
    ),
    ToolSpec(
        key="watermark",
        label="Watermark",
        description="Stamp a logo onto images, scaled and positioned.",
        screen_factory=_watermark_screen_factory,
        accent="#5EEAD4",  # teal
        help=(
            "Choose the photos, the logo (PNG or SVG), a position out of nine, a size (% of the photo's width), "
            "opacity and margin.\nRotated phone photos are straightened first so the logo lands in the visible corner."
        ),
        cli="mysuite watermark photo.jpg --logo logo.svg --position bottom-right --opacity 80",
    ),
    ToolSpec(
        key="metadata",
        label="Metadata",
        description="Strip hidden data, fake a camera, or credit the author.",
        screen_factory=_metadata_screen_factory,
        accent="#93C5FD",  # periwinkle
        help=(
            "Strip removes everything (GPS, serial numbers, camera, software). Randomize then writes one plausible "
            "decoy camera identity (never a location). Credit embeds author and copyright as a C2PA record "
            "(signed with a test certificate).\nWorks on copies: your original stays as it is."
        ),
        cli="mysuite metadata strip photo.jpg",
    ),
    ToolSpec(
        key="compress",
        label="Compress",
        description="Smaller files with mozjpeg, WebP, AVIF, oxipng, pngquant, gifsicle.",
        screen_factory=_compress_screen_factory,
        accent="#FDE047",  # amber-gold
        help=(
            "Pick a codec (or a preset) and optionally a quality. JPEG → mozjpeg, PNG → oxipng (lossless) or "
            "pngquant (lossy), plus WebP and AVIF. Optional sharpening before encoding.\nOutput gets "
            "_compressed in its name; you'll see the new size in the log."
        ),
        cli="mysuite compress photo.jpg --codec mozjpeg --quality 80",
    ),
    ToolSpec(
        key="enhance",
        label="Enhance",
        description="Upscale and restore photos locally: denoise, sharpen, scratches.",
        screen_factory=_enhance_screen_factory,
        accent="#F9A8D4",  # rose
        help=(
            "Pick a preset (gentle, prime, old_photo, portrait, ai_art) and a scale. It is classical image processing "
            "(no AI model unless you installed one), so it cleans and sharpens but cannot invent detail.\n"
            "'old_photo' also fills thin straight scratches. Output is name_enhanced.png."
        ),
        cli="mysuite enhance run old.jpg --preset old_photo --scale 2",
    ),
]
