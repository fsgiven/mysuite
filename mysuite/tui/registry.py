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
    # Tools without a hand-built screen get one generated from the CLI (screens/auto_screen.py):
    # (label shown in the "What do you want to do?" list, command path). Several entries = one card, many commands.
    commands: tuple[tuple[str, tuple[str, ...]], ...] = ()


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


def _auto_factory(key: str) -> Callable[[], Screen]:
    def factory() -> Screen:
        from mysuite.tui.screens.auto_screen import AutoToolScreen

        spec = next(s for s in TOOL_REGISTRY if s.key == key)
        return AutoToolScreen(spec)

    return factory


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
    ToolSpec(
        key="kits",
        label="Kits",
        description="A whole set from one logo: favicons, app icons, social images, retina.",
        screen_factory=_auto_factory("kits"),
        accent="#BEF264",  # lime
        commands=(("Make a kit from a logo", ("kit", "make")),),
        help=(
            "Choose a logo (SVG or image) and a kit: favicon (ico, PNGs, Apple/Android icons, web manifest, HTML snippet), "
            "ios-app-icon (every size + Contents.json), android-icons (every density), social (Open Graph, Twitter, "
            "LinkedIn, YouTube, Instagram sizes; needs a background colour) or retina (@1x/@2x/@3x).\n"
            "Every file has exactly the size its platform asks for, so there is nothing to review. Files go to "
            "kits/<logo name>/<kit>/ and nothing is overwritten unless you tick it."
        ),
        cli="mysuite kit make logo.svg --kit favicon",
    ),
    ToolSpec(
        key="pdf",
        label="PDF",
        description="Merge, split, rotate, number, stamp, compress; pages to images and back.",
        screen_factory=_auto_factory("pdf"),
        accent="#FDBA74",  # orange
        commands=(
            ("Merge PDFs", ("pdf", "merge")), ("Split into pieces", ("pdf", "split")), ("Keep only some pages", ("pdf", "extract")),
            ("Rotate pages", ("pdf", "rotate")), ("Fit pages to a paper size", ("pdf", "resize")), ("Add page numbers", ("pdf", "number")),
            ("Stamp text or a logo", ("pdf", "stamp")), ("Make smaller", ("pdf", "compress")), ("Remove metadata", ("pdf", "strip")),
            ("Pages to images", ("pdf", "render")), ("Extract embedded pictures", ("pdf", "images")),
            ("Images to a PDF", ("pdf", "from-images")), ("Show facts about a PDF", ("pdf", "info")),
        ),
        help=(
            "Pick what you want to do, then the PDF(s). Page ranges look like 1,3-5,8- (also last, odd, even). "
            "Paper sizes: a4, letter, 210x297mm …\nEvery job writes a new file next to the original (with a suffix like "
            "_merged or _rotated) and never changes the original. Password-protected PDFs are not opened."
        ),
        cli="mysuite pdf extract doc.pdf --pages 1,3-5",
    ),
    ToolSpec(
        key="exact",
        label="Exact",
        description="Exact sizes and specs: print size, rename, contact sheet, colour profile.",
        screen_factory=_auto_factory("exact"),
        accent="#F0ABFC",  # fuchsia
        commands=(
            ("Output at an exact size (px / cm / in)", ("print",)), ("Rename many files by a pattern", ("rename",)),
            ("Contact sheet of many pictures", ("sheet",)), ("Convert colour profile (sRGB / CMYK)", ("profile",)),
        ),
        help=(
            "Print: '10x15cm' at 300 dpi is exactly 1181x1772 px, with the dpi stored in the file; fit = contain, cover "
            "or stretch; bleed in mm.\nRename: tokens {name} {ext} {n} {n:3} {date} {w} {h}; makes copies unless you tick "
            "move; use the preview option first.\nSheet: one labelled picture of many.\nProfile: Adobe RGB / Display P3 / "
            "CMYK photos to a correct sRGB copy, or RGB to CMYK for print."
        ),
        cli="mysuite print photo.jpg --size 10x15cm --dpi 300",
    ),
    ToolSpec(
        key="helpers",
        label="Helpers",
        description="Read text (OCR), QR codes, find duplicates, compare images, check contrast.",
        screen_factory=_auto_factory("helpers"),
        accent="#A7F3D0",  # mint
        commands=(
            ("Read the text in an image (OCR)", ("ocr",)), ("Make a QR code", ("qr", "make")), ("Read a QR code / barcode", ("qr", "read")),
            ("Find duplicate pictures", ("dupes",)), ("Compare two images", ("diff",)), ("Check colour contrast", ("contrast",)),
        ),
        help=(
            "Read-only helpers: nothing is changed except the optional files you ask for. OCR and QR reading use macOS's "
            "built-in text recognition (the mysuite-vision helper). Duplicates are found by what pictures look like, so "
            "resized and re-saved copies are caught too. Contrast uses the WCAG formula (4.5:1 is the bar for body text)."
        ),
        cli="mysuite dupes ~/Pictures --recursive",
    ),
    ToolSpec(
        key="tokens",
        label="Tokens",
        description="Brand colours from your design system: logo variants, palette checks.",
        screen_factory=_auto_factory("tokens"),
        accent="#D6D3D1",  # stone
        commands=(
            ("Make logo variants (negative, mono, invert…)", ("variants", "make")),
            ("Check a logo's colours against the tokens", ("tokens", "check")),
            ("List the colour tokens", ("tokens", "list")),
            ("Show one token", ("tokens", "show")),
        ),
        help=(
            "Point at your design tokens: a CSS or JSON file/folder (Axis-style CSS variables and W3C design-token JSON "
            "both work), a pinned git repo (git+https://host/org/repo@v1#path=dir) or figma:FILEKEY. Pick a brand if the "
            "source has several.\nVariants writes SVG versions of your logo: 'negative' follows the tokens' dark-theme "
            "values, the others are invert, mono-black, mono-white, grayscale. Check tells you which logo colours are real "
            "token colours and which are off-palette. Then run Export on the variants."
        ),
        cli="mysuite variants make logo.svg --variants negative --tokens tokens/ --brand acme",
    ),
    ToolSpec(
        key="profiles",
        label="Profiles",
        description="A company's defaults in one place: tokens, brand, sizes, metadata policy.",
        screen_factory=_auto_factory("profiles"),
        accent="#FDA4AF",  # blush
        commands=(
            ("List profiles", ("profiles", "list")), ("Create or replace a profile", ("profiles", "save")),
            ("Show a profile", ("profiles", "show")), ("Check a profile works", ("profiles", "check")),
            ("Delete a profile", ("profiles", "delete")), ("Apply a metadata policy to photos", ("metadata", "apply")),
        ),
        help=(
            "A profile bundles what you repeat for one company: where its design tokens are, the brand, the logo variants, "
            "export sizes/formats/CMYK, the allowed folders, and what to do with photo metadata (for example strip it, then "
            "credit the company). Create one here, then use it from the command line with --profile NAME, from pipelines, "
            "or from AI agents. Options you set directly always win over the profile."
        ),
        cli="mysuite profiles save acme --tokens tokens/ --brand acme --policy strip,credit --author \"Acme\"",
    ),
    ToolSpec(
        key="shield",
        label="Shield",
        description="Experimental: make an image resist AI editing. Slow, needs a one-time download.",
        screen_factory=_auto_factory("shield"),
        accent="#C7D2FE",  # periwinkle-white
        commands=(("Protect images against AI editing", ("shield",)),),
        help=(
            "Adds a small invisible-ish pattern that confuses the image encoder behind Stable Diffusion-style editors, so an AI "
            "'edit' of your picture turns into something else. Output is a lossless PNG (_shielded). It takes about a minute per "
            "512 px tile, needs the shield component (Parts card, ~1.4 GB, fetched once), and is NOT a guarantee: resizing or "
            "denoising can remove it, and it targets one model family. Tick 'dry run' first for a time estimate. See docs/SHIELD.md."
        ),
        cli="mysuite shield photo.png --strength standard",
    ),
    ToolSpec(
        key="parts",
        label="Parts",
        description="Heavier parts, fetched once when you need them: helpers, shield environment.",
        screen_factory=_auto_factory("parts"),
        accent="#BAE6FD",  # ice
        commands=(("Install or list parts", ("install",)),),
        help=(
            "mysuite itself stays small. Bigger parts are downloaded or built only when you ask: 'shield' (about 1.4 GB: PyTorch in a "
            "private environment + model weights), and the macOS helpers 'vision' and 'cutout' (built from source with Xcode tools). "
            "Leave the name empty to list what is installed. Tick 'yes' to confirm an install, 'remove' to delete a part again. "
            "Everything lives in ~/.cache/mysuite."
        ),
        cli="mysuite install shield --yes",
    ),
]
