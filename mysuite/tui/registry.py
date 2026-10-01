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


def _transform_screen_factory() -> Screen:
    from mysuite.tui.screens.transform_screen import TransformScreen

    return TransformScreen()


TOOL_REGISTRY: list[ToolSpec] = [
    ToolSpec(
        key="export",
        label="Export",
        description="Batch-export an SVG into multiple sizes, formats, and color profiles.",
        screen_factory=_export_screen_factory,
        accent="#7DD3FC",  # cyan
    ),
    ToolSpec(
        key="convert",
        label="Convert",
        description="Convert a file to another format, in place beside the source.",
        screen_factory=_convert_screen_factory,
        accent="#C4B5FD",  # violet
    ),
    ToolSpec(
        key="cutout",
        label="Cutout",
        description="Isolate a photo's subject into a transparent-background PNG.",
        screen_factory=_cutout_screen_factory,
        accent="#FCA5A5",  # coral
    ),
    ToolSpec(
        key="watermark",
        label="Watermark",
        description="Stamp a logo onto image(s) as a visible, scalable watermark.",
        screen_factory=_watermark_screen_factory,
        accent="#5EEAD4",  # teal
    ),
    ToolSpec(
        key="metadata",
        label="Metadata",
        description="Strip all metadata, or embed a signed C2PA provenance record.",
        screen_factory=_metadata_screen_factory,
        accent="#93C5FD",  # periwinkle
    ),
    ToolSpec(
        key="compress",
        label="Compress",
        description="Re-encode via a best-in-class codec (mozjpeg/webp/avif/oxipng/pngquant/gifsicle), with sharpening.",
        screen_factory=_compress_screen_factory,
        accent="#FDE047",  # amber-gold
    ),
    ToolSpec(
        key="enhance",
        label="Enhance",
        description="Upscale and restore photos locally — denoise, sharpen, scratch removal, color.",
        screen_factory=_enhance_screen_factory,
        accent="#F9A8D4",  # rose
    ),
    ToolSpec(
        key="transform",
        label="Transform",
        description="Quick edits: trim, crop, rotate, flip, resize, pad, round corners, background.",
        screen_factory=_transform_screen_factory,
        accent="#FDBA74",  # orange
    ),
]
