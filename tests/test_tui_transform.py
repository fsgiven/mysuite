from __future__ import annotations

import pytest
from PIL import Image
from textual.widgets import Button, Checkbox, Input, Select

from mysuite.tui.app import MysuiteApp
from mysuite.tui.registry import TOOL_REGISTRY

KEYS = [s.key for s in TOOL_REGISTRY]


async def open_transform(pilot):
    await pilot.pause()
    await pilot.press(str(KEYS.index("transform") + 1))
    await pilot.pause()
    return pilot.app.screen


async def finish(pilot):
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()


def test_transform_is_registered_with_its_own_accent():
    spec = next(s for s in TOOL_REGISTRY if s.key == "transform")
    assert spec.label == "Transform" and spec.accent.startswith("#")
    assert len({s.accent for s in TOOL_REGISTRY}) == len(TOOL_REGISTRY)      # every tool keeps a distinct colour


@pytest.mark.asyncio
async def test_resize_and_pad_a_file_from_the_screen(tmp_path):
    src = tmp_path / "a.png"
    Image.new("RGB", (200, 100), (255, 0, 0)).save(src)
    app = MysuiteApp()
    async with app.run_test(size=(140, 50)) as pilot:
        screen = await open_transform(pilot)
        assert type(screen).__name__ == "TransformScreen"
        screen.query_one("#input-files", Input).value = str(src)
        screen.query_one("#resize", Input).value = "100"
        screen.query_one("#pad", Input).value = "1:1"
        screen.query_one("#pad-color", Input).value = "#00ff00"
        screen.action_run()
        await finish(pilot)
        out = tmp_path / "a_transformed.png"
        assert out.exists() and Image.open(out).size == (100, 100)
        assert Image.open(out).getpixel((2, 2))[:3] == (0, 255, 0)
        assert "1 written" in screen.last_summary_text
        assert src.exists() and Image.open(src).size == (200, 100)


@pytest.mark.asyncio
async def test_rotate_and_format_selects_and_trim_checkbox(tmp_path):
    src = tmp_path / "w.png"
    im = Image.new("RGB", (100, 100), "white")
    im.paste((255, 0, 0), (40, 40, 60, 80))
    im.save(src)
    app = MysuiteApp()
    async with app.run_test(size=(140, 50)) as pilot:
        screen = await open_transform(pilot)
        screen.query_one("#input-files", Input).value = str(src)
        screen.query_one("#trim", Checkbox).value = True
        screen.query_one("#rotate", Select).value = "90"
        screen.query_one("#format", Select).value = "webp"
        screen.action_run()
        await finish(pilot)
    out = tmp_path / "w_transformed.webp"
    assert out.exists() and Image.open(out).size == (40, 20)        # trim 20x40, rotate 90 -> 40x20


@pytest.mark.asyncio
async def test_bad_values_flash_the_field_and_run_nothing(tmp_path):
    src = tmp_path / "a.png"
    Image.new("RGB", (50, 50), (255, 0, 0)).save(src)
    app = MysuiteApp()
    async with app.run_test(size=(140, 50)) as pilot:
        screen = await open_transform(pilot)
        screen.action_run()                                          # nothing entered
        await pilot.pause()
        assert screen.query_one("#input-files").has_class("field-error")
        screen.query_one("#input-files", Input).value = str(src)
        screen.query_one("#resize", Input).value = "abc"
        screen.action_run()
        await finish(pilot)
        assert screen.query_one("#resize").has_class("field-error")
        screen.query_one("#resize", Input).value = ""
        screen.action_run()                                          # no edit chosen
        await finish(pilot)
    assert not list(tmp_path.glob("*_transformed*"))


@pytest.mark.asyncio
async def test_escape_goes_back_to_the_overview():
    app = MysuiteApp()
    async with app.run_test(size=(140, 50)) as pilot:
        await open_transform(pilot)
        await pilot.press("escape")
        await pilot.pause()
        assert type(app.screen).__name__ == "HomeScreen"
