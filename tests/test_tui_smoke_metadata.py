from __future__ import annotations

import shutil
import subprocess

import pytest
from textual.widgets import Checkbox, Input, Select, Static

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
        for value, visible in (("strip", False), ("declare", False), ("credit", True), ("protect", True)):
            mode.value = value
            await pilot.pause()
            assert screen.query_one("#group-credit").display is visible, value


@pytest.mark.slow
@skip_if_tools_missing
async def test_protect_run_writes_one_file_with_the_credit(tmp_path):
    photo = tmp_path / "photo.jpg"
    subprocess.run(["magick", "-size", "80x60", "xc:#3388ff", str(photo)], check=True)
    subprocess.run(["exiftool", "-q", "-overwrite_original", "-GPSLatitude=52.5", "-GPSLatitudeRef=N", str(photo)], check=True)

    async with MysuiteApp().run_test() as pilot:
        screen = await _open(pilot)
        screen.query_one("#input-files", Input).value = str(photo)
        screen.query_one("#mode", Select).value = "protect"
        screen.query_one("#author", Input).value = "Jane Doe"
        await pilot.pause()
        screen.action_run()
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()

        out = photo.with_name("photo_protected.jpg")
        assert out.exists() and sorted(p.name for p in tmp_path.iterdir()) == ["photo.jpg", "photo_protected.jpg"]
        assert "1 written" in str(screen.query_one("#run-summary", Static).render())
        t = subprocess.run(["exiftool", "-s3", "-IPTC:By-line", "-GPSLatitude", str(out)], capture_output=True, text=True).stdout
        assert t.strip() == "Jane Doe"


async def test_the_mark_key_goes_through_the_environment_not_the_command(tmp_path):
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"x")
    async with MysuiteApp().run_test() as pilot:
        screen = await _open(pilot)
        screen.query_one("#input-files", Input).value = str(photo)
        screen.query_one("#mode", Select).value = "protect"
        screen.query_one("#author", Input).value = "Jane Doe"
        screen.query_one("#mark", Checkbox).value = True
        screen.query_one("#key", Input).value = "correct horse battery"
        await pilot.pause()
        argv = screen.argv()
        assert "--mark" in argv and "correct horse battery" not in " ".join(argv)
        assert screen.child_env() == {"MYSUITE_MARK_KEY": "correct horse battery"}
        assert screen.query_one("#key", Input).password
        assert "key" not in screen.PERSIST
