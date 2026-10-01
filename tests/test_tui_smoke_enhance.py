from __future__ import annotations

import numpy as np
import pytest
from PIL import Image
from textual import events
from textual.widgets import Checkbox, Input, Select, Static

from mysuite.tui.app import MysuiteApp
from mysuite.tui.registry import TOOL_REGISTRY
from mysuite.tui.screens.enhance_screen import EnhanceScreen

_INDEX = next(i for i, s in enumerate(TOOL_REGISTRY, start=1) if s.key == "enhance")


@pytest.fixture
def photo(tmp_path):
    path = tmp_path / "photo.png"
    Image.fromarray(np.random.default_rng(0).integers(0, 255, (30, 40, 3), dtype=np.uint8)).save(path)
    return path


async def _open(pilot) -> EnhanceScreen:
    await pilot.pause()
    await pilot.press(str(_INDEX))
    await pilot.pause()
    assert isinstance(pilot.app.screen, EnhanceScreen)
    return pilot.app.screen


async def test_preset_fills_fields_and_switching_resets_the_previous_one():
    async with MysuiteApp().run_test() as pilot:
        screen = await _open(pilot)
        screen.query_one("#preset", Select).value = "old-photo"
        await pilot.pause()
        assert screen.query_one("#scale", Input).value == "2"
        assert screen.query_one("#denoise", Input).value == "0.7"
        assert screen.query_one("#restore-scratches", Checkbox).value is True

        screen.query_one("#preset", Select).value = "portrait"
        await pilot.pause()
        assert screen.query_one("#restore-scratches", Checkbox).value is False
        assert screen.query_one("#output-format", Select).value == "jpg"


async def test_run_writes_output_beside_source(photo):
    async with MysuiteApp().run_test() as pilot:
        screen = await _open(pilot)
        screen.query_one("#input-files", Input).value = str(photo)
        screen.query_one("#backend", Select).value = "classical"
        screen.query_one("#scale", Input).value = "2"
        screen.action_run()
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()
        out = photo.with_name("photo_enhanced.png")
        assert out.exists() and Image.open(out).size == (80, 60)
        assert "1 written" in str(screen.query_one("#run-summary", Static).render())


async def test_bad_number_flashes_the_field_and_does_not_run(photo):
    async with MysuiteApp().run_test() as pilot:
        screen = await _open(pilot)
        screen.query_one("#input-files", Input).value = str(photo)
        screen.query_one("#scale", Input).value = "two"
        screen.action_run()
        await pilot.pause()
        assert "field-error" in screen.query_one("#scale", Input).classes
        assert not photo.with_name("photo_enhanced.png").exists()


async def test_out_of_range_value_is_reported_and_does_not_run(photo):
    async with MysuiteApp().run_test() as pilot:
        screen = await _open(pilot)
        screen.query_one("#input-files", Input).value = str(photo)
        screen.query_one("#scale", Input).value = "99"
        screen.action_run()
        await pilot.pause()
        assert not photo.with_name("photo_enhanced.png").exists()


async def test_pasting_a_dropped_photo_fills_the_input(photo):
    async with MysuiteApp().run_test() as pilot:
        screen = await _open(pilot)
        field = screen.query_one("#input-files", Input)
        field.focus()
        field.post_message(events.Paste(text=str(photo)))
        await pilot.pause()
        assert field.value == str(photo)
