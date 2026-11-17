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


# --------------------------------------------------------------------------- enhance
async def test_enhance_form_starts_from_what_the_cli_really_runs():
    """No preset = the CLI's default, gentle: auto white balance is OFF there, so the box must not be ticked."""
    async with MysuiteApp().run_test() as pilot:
        screen = await _open(pilot, "enhance")
        assert screen.query_one("#auto-white-balance", Checkbox).value is False
        assert screen.query_one("#scale", Input).placeholder == "2"
        assert screen.query_one("#denoise", Input).placeholder == "0.15"


async def test_enhance_ticking_white_balance_without_a_preset_adds_the_flag(tmp_path):
    photo = tmp_path / "p.jpg"
    Image.new("RGB", (20, 20), "white").save(photo)
    async with MysuiteApp().run_test() as pilot:
        screen = await _open(pilot, "enhance")
        screen.query_one("#input-files", Input).value = str(photo)
        assert "--auto-white-balance" not in screen.argv()
        screen.query_one("#auto-white-balance", Checkbox).value = True
        await pilot.pause()
        assert "--auto-white-balance" in screen.argv()


async def test_enhance_preset_with_white_balance_ticks_it_and_unticking_turns_it_off(tmp_path):
    photo = tmp_path / "p.jpg"
    Image.new("RGB", (20, 20), "white").save(photo)
    async with MysuiteApp().run_test() as pilot:
        screen = await _open(pilot, "enhance")
        screen.query_one("#input-files", Input).value = str(photo)
        screen.query_one("#preset", Select).value = "old-photo"
        await pilot.pause()
        assert screen.query_one("#auto-white-balance", Checkbox).value is True
        screen.query_one("#auto-white-balance", Checkbox).value = False
        await pilot.pause()
        assert "--no-auto-white-balance" in screen.argv()
