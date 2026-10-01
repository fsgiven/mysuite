from __future__ import annotations

import tempfile
from pathlib import Path

from PIL import Image
from rich.style import Style
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from mysuite.config import ToolPaths
from mysuite.export.renderer import _rsvg_render_png_dims

# Matches theme.css's Screen background (#10121A) — transparent SVG regions are
# composited onto this so the thumbnail blends into the TUI's dark theme
# instead of rendering as flat black.
PREVIEW_BACKGROUND = (16, 18, 26)


def render_svg_thumbnail(svg_path: Path, tools: ToolPaths, *, size_px: int = 128, dpi: float = 96) -> Path:
    """Rasterizes svg_path to a small square PNG for preview purposes, reusing
    the same rsvg-convert invocation the real export pipeline uses. The caller
    owns the returned temp file and is responsible for deleting it."""
    handle = tempfile.NamedTemporaryFile(
        suffix=".png", prefix=f"mysuite-preview-{svg_path.stem}-", delete=False
    )
    handle.close()
    output_path = Path(handle.name)
    _rsvg_render_png_dims(svg_path, output_path, str(size_px), str(size_px), dpi, tools)
    return output_path


def image_to_halfblock_renderable(image: Image.Image, width: int, height: int) -> Text:
    """Renders a PIL image as a grid of Unicode upper-half-block characters
    (▀), two source pixel rows per terminal cell — the foreground color is the
    top pixel, the background color is the bottom pixel — giving roughly 2x
    vertical resolution per cell. This needs no terminal graphics protocol
    (Kitty/Sixel/iTerm2 inline images), so it renders consistently everywhere,
    including plain SSH sessions."""
    if width <= 0 or height <= 0:
        return Text("")

    resized = image.convert("RGBA").resize((width, height * 2))
    canvas = Image.new("RGBA", resized.size, (*PREVIEW_BACKGROUND, 255))
    canvas.alpha_composite(resized)
    pixels = canvas.convert("RGB").load()

    result = Text()
    for row in range(height):
        if row:
            result.append("\n")
        top_y = row * 2
        bottom_y = top_y + 1
        for col in range(width):
            top = pixels[col, top_y]
            bottom = pixels[col, bottom_y]
            style = Style(
                color=f"rgb({top[0]},{top[1]},{top[2]})",
                bgcolor=f"rgb({bottom[0]},{bottom[1]},{bottom[2]})",
            )
            result.append("▀", style=style)
    return result


class SvgThumbnail(Vertical):
    """A small preview tile: a half-block-rendered raster of one SVG file, with
    its filename shown underneath. Rendering happens in on_mount from an
    already-rasterized PNG path — callers are responsible for generating that
    PNG off the UI thread (rsvg-convert is a blocking subprocess call)."""

    def __init__(
        self, png_path: Path, name: str, *, cell_width: int = 20, cell_height: int = 10
    ) -> None:
        super().__init__(classes="svg-thumbnail")
        self._png_path = png_path
        self._name = name
        self._cell_width = cell_width
        self._cell_height = cell_height

    def compose(self) -> ComposeResult:
        # "auto" width/height on a Static measures its INITIAL content, which
        # is empty here (the real content only arrives later via .update() in
        # on_mount) — that collapses the widget to a zero-size layout box that
        # never grows even after the content is set. Sizing explicitly from
        # the already-known cell dimensions avoids that auto-measurement trap.
        self.styles.width = self._cell_width
        image = Static(id="thumb-image")
        image.styles.width = self._cell_width
        image.styles.height = self._cell_height
        yield image
        yield Static(self._name, id="thumb-label")

    def on_mount(self) -> None:
        try:
            with Image.open(self._png_path) as img:
                renderable = image_to_halfblock_renderable(img, self._cell_width, self._cell_height)
        except OSError:
            renderable = Text("(preview failed)")
        self.query_one("#thumb-image", Static).update(renderable)
