from __future__ import annotations

import shutil
import subprocess

import pytest
from textual.widgets import Input, Select, Static

from mysuite.tui.app import MysuiteApp
from mysuite.tui.registry import TOOL_REGISTRY
from mysuite.tui.screens.metadata_screen import MetadataScreen

skip_if_tools_missing = pytest.mark.skipif(
    shutil.which("exiftool") is None or shutil.which("magick") is None,
    reason="requires exiftool and magick",
)
_INDEX = next(i for i, s in enumerate(TOOL_REGISTRY, start=1) if s.key == "metadata")


async def _open(pilot) -> MetadataScreen:
    await pilot.pause()
    await pilot.press(str(_INDEX))
    await pilot.pause()
    assert isinstance(pilot.app.screen, MetadataScreen)
    return pilot.app.screen


async def test_credit_fields_only_visible_in_credit_mode():
    async with MysuiteApp().run_test() as pilot:
        screen = await _open(pilot)
        mode = screen.query_one("#mode", Select)
        for value, visible in (("strip", False), ("randomize", False), ("credit", True)):
            mode.value = value
            await pilot.pause()
            assert screen.query_one("#group-credit").display is visible, value


@pytest.mark.slow
@skip_if_tools_missing
async def test_randomize_run_writes_output_beside_source(tmp_path):
    photo = tmp_path / "photo.jpg"
    subprocess.run(["magick", "-size", "80x60", "xc:#3388ff", str(photo)], check=True)
    subprocess.run(["exiftool", "-q", "-overwrite_original", "-Artist=Frank", str(photo)], check=True)

    async with MysuiteApp().run_test() as pilot:
        screen = await _open(pilot)
        screen.query_one("#input-files", Input).value = str(photo)
        screen.query_one("#mode", Select).value = "randomize"
        await pilot.pause()
        screen.action_run()
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()

        out = photo.with_name("photo_randomized.jpg")
        assert out.exists()
        assert "1 written" in str(screen.query_one("#run-summary", Static).render())
        artist = subprocess.run(["exiftool", "-s3", "-Artist", str(out)], capture_output=True, text=True).stdout.strip()
        assert artist == ""
