"""Pilot tests for Export, Convert, Cutout and Watermark screens. Phase 0."""
from __future__ import annotations

import shutil
import subprocess

import pytest
from textual.widgets import Button, Input, RichLog, SelectionList, Select, Static

from mysuite.tui.app import MysuiteApp
from mysuite.tui.registry import TOOL_REGISTRY
from tests.helpers import SIMPLE_SVG

pytestmark = pytest.mark.slow
need_tools = pytest.mark.skipif(any(shutil.which(t) is None for t in ("rsvg-convert", "gs", "magick")), reason="needs tools")
KEYS = [s.key for s in TOOL_REGISTRY]


async def open_tool(pilot, key):
    await pilot.pause()
    pilot.app.screen._open_tool(key)          # digit keys only reach the first nine cards; this works for all
    await pilot.pause()
    return pilot.app.screen


async def finish(pilot):
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()


def log_text(screen) -> str:
    return "\n".join(str(l) for l in screen.query_one("#run-log", RichLog).lines)


@pytest.fixture
def svg(tmp_path):
    p = tmp_path / "logo.svg"
    p.write_text(SIMPLE_SVG)
    return p


@pytest.fixture
def photo(tmp_path):
    p = tmp_path / "photo.png"
    subprocess.run(["magick", "-size", "120x80", "gradient:red-blue", "-type", "TrueColor", f"PNG24:{p}"], check=True)
    return p


@need_tools
async def test_export_screen_runs_and_writes_files(svg, tmp_path):
    async with MysuiteApp().run_test() as pilot:
        sc = await open_tool(pilot, "export")
        sc.query_one("#input-svg", Input).value = str(svg)
        sc.query_one("#sizes", Input).value = "32,64"
        sc.query_one("#out-dir", Input).value = str(tmp_path / "out")
        sc.action_run()
        await finish(pilot)
        files = sorted(p.name for p in (tmp_path / "out").rglob("*.png"))
        assert files == ["logo_32.png", "logo_64.png"], files
        assert "written" in str(sc.query_one("#run-summary", Static).render())


@need_tools
async def test_export_screen_dry_run_checkbox_writes_nothing(svg, tmp_path):
    async with MysuiteApp().run_test() as pilot:
        sc = await open_tool(pilot, "export")
        sc.query_one("#input-svg", Input).value = str(svg)
        sc.query_one("#out-dir", Input).value = str(tmp_path / "out")
        sc.query_one("#dry-run").value = True
        sc.action_run()
        await finish(pilot)
        assert not (tmp_path / "out").exists() and "dry run" in log_text(sc)


async def test_export_screen_bad_inputs_flash_errors_and_do_not_run(tmp_path):
    async with MysuiteApp().run_test() as pilot:
        sc = await open_tool(pilot, "export")
        sc.query_one("#input-svg", Input).value = str(tmp_path / "missing.svg")
        await pilot.pause()                       # typing settles before Run is pressed
        sc.action_run()
        await pilot.pause()
        assert "field-error" in sc.query_one("#input-svg", Input).classes


@need_tools
async def test_export_recolor_rows_apply(svg, tmp_path):
    from tests.helpers import pixel_rgb
    async with MysuiteApp().run_test() as pilot:
        sc = await open_tool(pilot, "export")
        sc.query_one("#input-svg", Input).value = str(svg)
        sc.query_one("#sizes", Input).value = "100"
        sc.query_one("#out-dir", Input).value = str(tmp_path / "out")
        from mysuite.tui.widgets.recolor_row import RecolorRow
        row = sc.query_one("#recolor-rows").query(RecolorRow).first()
        row.from_input.value = "#dd0000"
        row.to_input.value = "#00aa00"
        sc.action_run()
        await finish(pilot)
        (png,) = (tmp_path / "out").rglob("*.png")
        assert pixel_rgb(png, 10, 25)[1] > 120


@need_tools
async def test_convert_screen_runs(photo):
    async with MysuiteApp().run_test() as pilot:
        sc = await open_tool(pilot, "convert")
        sc.query_one("#input-files", Input).value = str(photo)
        sc.query_one("#target-format", Select).value = "webp"
        sc.action_run()
        await finish(pilot)
        assert photo.with_suffix(".webp").exists() and "1 written" in str(sc.query_one("#run-summary", Static).render())


@need_tools
async def test_convert_screen_same_format_is_reported_not_crashed(photo):
    async with MysuiteApp().run_test() as pilot:
        sc = await open_tool(pilot, "convert")
        sc.query_one("#input-files", Input).value = str(photo)
        sc.query_one("#target-format", Select).value = "png"
        sc.action_run()
        await finish(pilot)
        assert "1 failed" in str(sc.query_one("#run-summary", Static).render())


@need_tools
async def test_watermark_screen_runs_and_requires_a_logo(photo, tmp_path):
    logo = tmp_path / "logo.png"
    subprocess.run(["magick", "-size", "30x30", "xc:red", str(logo)], check=True)
    async with MysuiteApp().run_test() as pilot:
        sc = await open_tool(pilot, "watermark")
        sc.query_one("#input-files", Input).value = str(photo)
        sc.action_run()
        await pilot.pause()
        assert "field-error" in sc.query_one("#logo", Input).classes
        sc.query_one("#logo", Input).value = str(logo)
        sc.action_run()
        await finish(pilot)
        assert (tmp_path / "photo_watermarked.png").exists()


@pytest.mark.skipif(shutil.which("mysuite-cutout") is None, reason="needs mysuite-cutout")
async def test_cutout_screen_reports_failures_without_crashing(tmp_path):
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not an image")
    async with MysuiteApp().run_test() as pilot:
        sc = await open_tool(pilot, "cutout")
        sc.query_one("#input-files", Input).value = str(bad)
        sc.action_run()
        await finish(pilot)
        assert "1 failed" in str(sc.query_one("#run-summary", Static).render())


async def test_every_screen_opens_and_goes_back_with_escape():
    async with MysuiteApp().run_test() as pilot:
        for key in KEYS:
            sc = await open_tool(pilot, key)
            assert type(sc).__name__.lower().startswith(key) or type(sc).__name__ == "AutoToolScreen"
            await pilot.press("escape")
            await pilot.pause()
            assert type(pilot.app.screen).__name__ == "HomeScreen"


async def test_run_button_is_reenabled_after_a_failed_run(tmp_path):
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"x")
    async with MysuiteApp().run_test() as pilot:
        sc = await open_tool(pilot, "enhance")
        sc.query_one("#input-files", Input).value = str(bad)
        sc.action_run()
        await finish(pilot)
        assert sc.query_one("#run-btn", Button).disabled is False
