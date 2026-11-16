"""The dashboard's professional look: one accent, a title bar, no mascot by default, readable labels, clear run feedback."""
from __future__ import annotations

import shutil

import pytest
from textual.widgets import Checkbox, Input, Static

from mysuite.tui import style
from mysuite.tui.app import MysuiteApp
from mysuite.tui.screens.auto_screen import _checkbox_text, _label
from mysuite.tui.widgets.app_bar import AppBar
from mysuite.tui.widgets.card import two_lines
from tests.helpers import SIMPLE_SVG

need_tools = pytest.mark.skipif(any(shutil.which(t) is None for t in ("rsvg-convert", "magick")), reason="needs tools")


def test_tui_command_starts_on_the_tools_without_the_welcome(monkeypatch):
    from typer.testing import CliRunner

    import mysuite.tui.app as app_mod
    from mysuite.cli import app

    seen = {}
    monkeypatch.setattr(app_mod.MysuiteApp, "run", lambda self: seen.setdefault("welcome", self._show_welcome))
    assert CliRunner().invoke(app, ["tui"]).exit_code == 0 and seen["welcome"] is False
    seen.clear()
    assert CliRunner().invoke(app, ["tui", "--welcome"]).exit_code == 0 and seen["welcome"] is True


@pytest.mark.asyncio
async def test_title_bar_names_the_screen_and_shows_the_version():
    app = MysuiteApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        bar = str(app.screen.query_one(AppBar).render())
        assert bar.startswith("mysuite") and "Tools" in bar and f"v{style.version()}" in bar
        app.screen._open_tool("export")
        await pilot.pause(0.5)
        assert "Export" in str(app.screen.query_one(AppBar).render())


@pytest.mark.asyncio
async def test_help_is_a_side_panel_without_the_mascot():
    app = MysuiteApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.press("h")
        await pilot.pause(0.4)
        helper = app.screen
        assert type(helper).__name__ == "HelperScreen"
        assert not helper.query("#helper-face")
        panel = helper.query_one("#helper-panel")
        assert panel.region.x > 40 and panel.region.height >= 30          # right-hand panel, full height
        assert "mysuite --help" in str(helper.query_one("#helper-cli", Static).render())


@pytest.mark.asyncio
async def test_tick_boxes_show_a_tick_only_when_on():
    app = MysuiteApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        app.screen._open_tool("export")
        await pilot.pause(0.5)
        box = app.screen.query_one("#recursive", Checkbox)
        assert box.BUTTON_INNER == "✓"
        off = box.get_component_rich_style("toggle--button")
        box.value = True
        await pilot.pause()
        on = box.get_component_rich_style("toggle--button")
        assert off.color == off.bgcolor and on.color != on.bgcolor        # invisible when off, visible when on


@pytest.mark.parametrize("name,label", [("files", "Input files"), ("out", "Output"), ("keep_ids", "Keep ids"),
                                        ("overwrite", "Overwrite existing files")])
def test_generated_screens_use_readable_labels(name, label):
    assert _label(name) == label


def test_tick_box_text_is_one_sentence():
    assert _checkbox_text("overwrite", "Overwrite existing outputs.") == "Overwrite existing outputs"
    assert _checkbox_text("all_pages", "Import every page.") == "All pages — import every page"
    assert _checkbox_text("strict", "") == "Strict"


def test_card_text_is_two_lines_cut_between_words():
    text = two_lines("One SVG logo into many sizes, formats and colour profiles.", 20)
    lines = text.split("\n")
    assert len(lines) == 2 and all(len(l) <= 20 for l in lines) and lines[1].endswith("…")
    assert not lines[1][:-1].endswith(" ") and lines[1][:-1].split()[-1] in "One SVG logo into many sizes, formats and colour".split()
    assert two_lines("Short.", 20) == "Short."


@need_tools
@pytest.mark.asyncio
async def test_a_run_ends_with_a_marked_timed_summary_and_coloured_lines(tmp_path):
    svg = tmp_path / "logo.svg"
    svg.write_text(SIMPLE_SVG)
    app = MysuiteApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        app.screen._open_tool("export")
        await pilot.pause(0.5)
        sc = app.screen
        sc.query_one("#input-svg", Input).value = str(svg)
        sc.query_one("#sizes", Input).value = "64"
        sc.query_one("#out-dir", Input).value = str(tmp_path / "out")
        sc.query_one("#formats-png", Checkbox).value = True
        for f in ("pdf", "eps", "svg", "jpeg", "webp", "tiff", "ico", "icns"):
            sc.query_one(f"#formats-{f}", Checkbox).value = False
        await pilot.pause(0.6)
        assert not sc.query_one("#run-progress").display                 # no idle bar under the command
        await pilot.press("ctrl+r")
        for _ in range(60):
            await pilot.pause(0.2)
            if not sc._busy:
                break
        summary = str(sc.query_one("#run-summary", Static).render())
        assert summary.startswith("✓ done — 1 written") and summary.rstrip().endswith(" s")
        assert sc.query_one("#after-run").display                         # Open output folder
