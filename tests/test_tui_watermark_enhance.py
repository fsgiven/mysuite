"""WS-C1 items 4 and 5 in the dashboard: watermark size-by/quality; enhance defaults that match the preset."""
from __future__ import annotations

from pathlib import Path

from PIL import Image
from textual.widgets import Checkbox, Input, Select

from mysuite.tui.app import MysuiteApp
from mysuite.tui.registry import TOOL_REGISTRY


def _index(key: str) -> str:
    return str(next(i for i, s in enumerate(TOOL_REGISTRY, start=1) if s.key == key))


async def _open(pilot, key: str):
    await pilot.pause()
    await pilot.press(_index(key))
    await pilot.pause()
    return pilot.app.screen


async def test_watermark_form_passes_size_by_and_quality(tmp_path):
    photo, logo = tmp_path / "p.jpg", tmp_path / "logo.png"
    Image.new("RGB", (40, 30), "white").save(photo)
    Image.new("RGBA", (10, 10), "red").save(logo)
    async with MysuiteApp().run_test() as pilot:
        screen = await _open(pilot, "watermark")
        screen.query_one("#input-files", Input).value = str(photo)
        screen.query_one("#logo", Input).value = str(logo)
        screen.query_one("#size-by", Select).value = "height"
        screen.query_one("#quality", Input).value = "85"
        await pilot.pause()
        argv = screen.argv()
        assert argv[argv.index("--size-by") + 1] == "height"
        assert argv[argv.index("--quality") + 1] == "85"
