"""WS-C1 item 2 in the dashboard: Save for web on the Compress screen, reachable from Convert."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from textual.widgets import Button, Input, Select, Static

from mysuite.config import ToolPaths
from mysuite.tui.app import MysuiteApp
from mysuite.tui.registry import TOOL_REGISTRY
from mysuite.tui.screens.compress_screen import CompressScreen
from mysuite.tui.screens.convert_screen import ConvertScreen
from mysuite.tui.shell import FormError

need_mozjpeg = pytest.mark.skipif(not Path(ToolPaths().cjpeg).exists(), reason="requires mozjpeg")


def _index(key: str) -> str:
    return str(next(i for i, s in enumerate(TOOL_REGISTRY, start=1) if s.key == key))


def _photo(path: Path) -> Path:
    rng = np.random.default_rng(5)
    y, x = np.mgrid[0:600, 0:900]
    rgb = np.dstack([x * 255 / 900, y * 255 / 600, np.full_like(x, 120.0)]) + rng.normal(0, 14, (600, 900, 3))
    Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8)).save(path)
    return path


async def _open(pilot, key: str):
    await pilot.pause()
    await pilot.press(_index(key))
    await pilot.pause()
    return pilot.app.screen


async def test_compress_form_has_save_for_web_fields(tmp_path):
    src = _photo(tmp_path / "p.png")
    async with MysuiteApp().run_test() as pilot:
        screen = await _open(pilot, "compress")
        screen.query_one("#input-files", Input).value = str(src)
        screen.query_one("#max-kb", Input).value = "300"
        screen.query_one("#max-size", Input).value = "1000"
        screen.query_one("#background", Input).value = "#eeeeee"
        argv = screen.argv()
        assert argv[argv.index("--max-kb") + 1] == "300"
        assert argv[argv.index("--max-size") + 1] == "1000"
        assert argv[argv.index("--background") + 1] == "#eeeeee"


async def test_max_kb_with_a_lossless_codec_is_a_form_error(tmp_path):
    src = _photo(tmp_path / "p.png")
    async with MysuiteApp().run_test() as pilot:
        screen = await _open(pilot, "compress")
        screen.query_one("#input-files", Input).value = str(src)
        screen.query_one("#codec", Select).value = "oxipng"
        screen.query_one("#max-kb", Input).value = "300"
        await pilot.pause()
        with pytest.raises(FormError, match="JPEG, WebP or AVIF"):
            screen.argv()


async def test_save_for_web_preset_fills_the_limits():
    async with MysuiteApp().run_test() as pilot:
        screen = await _open(pilot, "compress")
        screen.query_one("#preset", Select).value = "save-for-web"
        await pilot.pause()
        assert screen.query_one("#codec", Select).value == "mozjpeg"
        assert screen.query_one("#max-kb", Input).value == "500"
        assert screen.query_one("#max-size", Input).value == "2048"


@need_mozjpeg
async def test_run_shows_bytes_before_and_after(tmp_path):
    src = _photo(tmp_path / "p.png")
    async with MysuiteApp().run_test() as pilot:
        screen = await _open(pilot, "compress")
        screen.query_one("#input-files", Input).value = str(src)
        screen.query_one("#max-kb", Input).value = "100"
        screen.action_run()
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()
        summary = str(screen.query_one("#run-summary", Static).render())
        assert "1 written" in summary and "→" in summary and "KB" in summary and "smaller" in summary
        assert Path(src.with_name("p_compressed.jpg")).stat().st_size <= 100_000


async def test_convert_offers_save_for_web_with_the_same_files(tmp_path):
    src = _photo(tmp_path / "p.png")
    async with MysuiteApp().run_test() as pilot:
        screen = await _open(pilot, "convert")
        assert isinstance(screen, ConvertScreen)
        screen.query_one("#input-files", Input).value = str(src)
        await pilot.pause()
        screen.query_one("#save-for-web", Button).press()
        await pilot.pause()
        await pilot.pause()
        web = pilot.app.screen
        assert isinstance(web, CompressScreen)
        assert web.query_one("#input-files", Input).value == str(src)
        assert web.query_one("#preset", Select).value == "save-for-web"
        assert web.query_one("#max-kb", Input).value == "500"
