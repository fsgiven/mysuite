from __future__ import annotations

import shutil
import subprocess

import pytest
from textual import events
from textual.widgets import Input, Select, Static

from mysuite.tui.app import MysuiteApp
from mysuite.tui.registry import TOOL_REGISTRY
from mysuite.tui.screens.compress_screen import CompressScreen
from mysuite.tui.screens.home import HomeScreen

skip_if_magick_missing = pytest.mark.skipif(
    shutil.which("magick") is None, reason="requires magick (brew install imagemagick)"
)
skip_if_mozjpeg_missing = pytest.mark.skipif(
    not shutil.which("/opt/homebrew/opt/mozjpeg/bin/cjpeg"),
    reason="requires mozjpeg (brew install mozjpeg)",
)

_COMPRESS_INDEX = next(i for i, spec in enumerate(TOOL_REGISTRY, start=1) if spec.key == "compress")


@pytest.fixture
def photo(tmp_path):
    path = tmp_path / "photo.png"
    subprocess.run(["magick", "-size", "200x200", "xc:#3388ff", str(path)], check=True)
    return path


async def _open_compress_screen(pilot) -> CompressScreen:
    await pilot.pause()
    assert isinstance(pilot.app.screen, HomeScreen)
    await pilot.press(str(_COMPRESS_INDEX))
    await pilot.pause()
    screen = pilot.app.screen
    assert isinstance(screen, CompressScreen)
    return screen


async def test_navigate_home_to_compress():
    app = MysuiteApp()
    async with app.run_test() as pilot:
        screen = await _open_compress_screen(pilot)
        assert screen.query_one("#input-files", Input) is not None


async def test_codec_default_is_mozjpeg_and_switching_toggles_groups():
    app = MysuiteApp()
    async with app.run_test() as pilot:
        screen = await _open_compress_screen(pilot)

        assert screen.query_one("#codec", Select).value == "mozjpeg"
        assert screen.query_one("#group-mozjpeg").display is True
        assert screen.query_one("#group-webp").display is False

        screen.query_one("#codec", Select).value = "webp"
        await pilot.pause()
        assert screen.query_one("#group-mozjpeg").display is False
        assert screen.query_one("#group-webp").display is True


@pytest.mark.slow
@skip_if_magick_missing
@skip_if_mozjpeg_missing
async def test_run_writes_output_beside_source(photo):
    app = MysuiteApp()
    async with app.run_test() as pilot:
        screen = await _open_compress_screen(pilot)

        screen.query_one("#input-files", Input).value = str(photo)
        screen.query_one("#quality", Input).value = "80"
        screen.action_run()
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()

        expected = photo.with_name("photo_compressed.jpg")
        assert expected.exists()
        assert "1 written" in str(screen.query_one("#run-summary", Static).render())


async def test_pasting_dropped_photo_populates_input_files(tmp_path):
    png = tmp_path / "photo.png"
    png.write_bytes(b"\x89PNG")

    app = MysuiteApp()
    async with app.run_test() as pilot:
        screen = await _open_compress_screen(pilot)

        field = screen.query_one("#input-files", Input)
        field.focus()
        field.post_message(events.Paste(text=str(png)))
        await pilot.pause()

        assert field.value == str(png)


async def test_selecting_preset_fills_codec_and_fields():
    app = MysuiteApp()
    async with app.run_test() as pilot:
        screen = await _open_compress_screen(pilot)

        screen.query_one("#preset", Select).value = "modern-web-avif"
        await pilot.pause()

        assert screen.query_one("#codec", Select).value == "avif"
        assert screen.query_one("#quality", Input).value == "55"
        assert screen.query_one("#speed", Input).value == "6"
        assert screen.query_one("#group-avif").display is True


async def test_switching_preset_resets_fields_not_in_new_preset():
    app = MysuiteApp()
    async with app.run_test() as pilot:
        screen = await _open_compress_screen(pilot)

        screen.query_one("#preset", Select).value = "modern-web-avif"
        await pilot.pause()
        assert screen.query_one("#quality", Input).value == "55"

        screen.query_one("#preset", Select).value = "lossless-archive"
        await pilot.pause()

        assert screen.query_one("#codec", Select).value == "oxipng"
        assert screen.query_one("#quality", Input).value == ""  # not part of this preset


async def test_manual_edit_after_preset_overrides_it():
    app = MysuiteApp()
    async with app.run_test() as pilot:
        screen = await _open_compress_screen(pilot)

        screen.query_one("#preset", Select).value = "web-photo-balanced"
        await pilot.pause()
        assert screen.query_one("#quality", Input).value == "82"

        screen.query_one("#quality", Input).value = "10"
        await pilot.pause()

        assert screen.query_one("#quality", Input).value == "10"
