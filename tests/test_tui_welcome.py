from __future__ import annotations

import pytest
from textual.widgets import Static

from mysuite.tui import art
from mysuite.tui.app import MysuiteApp
from mysuite.tui.registry import TOOL_REGISTRY
from mysuite.tui.screens.welcome import TIPS
from mysuite.tui.widgets.card import ToolCard
from mysuite.tui.widgets.scene import Scene


# ----------------------------------------------------------------------------- art
@pytest.mark.parametrize("face", list(art.FACES.values()))
def test_mascot_rows_are_the_same_width_in_every_frame(face):
    assert {len(r) for r in art.big_mascot_rows(face)} == {art.BIG_W}
    assert len({len(r) for r in art.small_mascot_rows(face)}) == 1


def test_big_mascot_has_two_handles_on_top():
    rows = art.big_mascot_rows()
    assert rows[0].count("╭") == 2 and rows[0].count("╮") == 2          # two handle tops
    assert rows[2].count("┴") == 4                                      # four handle feet on the lid


def test_every_tool_has_a_three_line_even_icon():
    for spec in TOOL_REGISTRY:
        rows = art.icon_rows(spec.key)
        assert len(rows) == 3 and len({len(r) for r in rows}) == 1, spec.key


# ---------------------------------------------------------------------------- scene
def test_scene_frame_is_exactly_the_requested_size_and_draws_the_mascot():
    scene = Scene(TIPS)
    scene._spawn(120, 40)
    for tick in (1, 17, 39):
        scene.tick_count = tick
        scene._typed = 10
        lines = scene.compose_frame(120, 40).plain.split("\n")
        assert len(lines) == 40 and {len(line) for line in lines} == {120}
        assert any("m y s u i t e" in line for line in lines)
        assert sum(line.count("╭") for line in lines[:15]) >= 2          # handles are on screen


def test_scene_survives_tiny_and_huge_sizes():
    scene = Scene(TIPS)
    for w, h in ((20, 8), (60, 20), (300, 90)):
        scene._spawn(w, h)
        assert len(scene.compose_frame(w, h).plain.split("\n")) == h


@pytest.mark.asyncio
async def test_welcome_animates_and_any_key_opens_the_overview():
    app = MysuiteApp(show_welcome=True)
    async with app.run_test(size=(110, 36)) as pilot:
        await pilot.pause(0.4)
        assert type(app.screen).__name__ == "WelcomeScreen"
        scene = app.screen.query_one(Scene)
        first = scene.tick_count
        await pilot.pause(0.5)
        assert scene.tick_count > first                                  # it is moving
        await pilot.press("x")
        await pilot.pause()
        assert type(app.screen).__name__ == "HomeScreen"


@pytest.mark.asyncio
async def test_default_app_still_opens_on_the_overview():
    app = MysuiteApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert type(app.screen).__name__ == "HomeScreen"


# ----------------------------------------------------------------------------- cards
@pytest.mark.asyncio
async def test_overview_shows_one_card_per_tool_in_a_grid():
    app = MysuiteApp()
    async with app.run_test(size=(130, 40)) as pilot:
        await pilot.pause()
        cards = list(app.screen.query(ToolCard))
        assert [c.spec.key for c in cards] == [s.key for s in TOOL_REGISTRY]
        xs = {c.region.x for c in cards}
        assert len(xs) >= 3                                              # several columns, not a single list


@pytest.mark.asyncio
async def test_arrows_move_between_cards_and_enter_opens_the_focused_one():
    app = MysuiteApp()
    async with app.run_test(size=(130, 40)) as pilot:
        await pilot.pause()
        assert app.screen.query(ToolCard).first().has_focus
        await pilot.press("right")
        assert list(app.screen.query(ToolCard))[1].has_focus
        await pilot.press("down")
        cols = app.screen._columns
        assert list(app.screen.query(ToolCard))[1 + cols].has_focus
        await pilot.press("up", "left")
        assert list(app.screen.query(ToolCard))[0].has_focus
        await pilot.press("right", "enter")
        await pilot.pause()
        assert type(app.screen).__name__ == "ConvertScreen"


@pytest.mark.asyncio
async def test_number_keys_and_clicks_still_open_tools():
    app = MysuiteApp()
    async with app.run_test(size=(130, 40)) as pilot:
        await pilot.pause()
        await pilot.press("3")
        await pilot.pause()
        assert type(app.screen).__name__ == "CutoutScreen"
        await pilot.press("escape")
        await pilot.pause()
        await pilot.click("#tool-watermark")
        await pilot.pause()
        assert type(app.screen).__name__ == "WatermarkScreen"


@pytest.mark.asyncio
async def test_narrow_terminals_get_fewer_columns():
    app = MysuiteApp()
    async with app.run_test(size=(70, 40)) as pilot:
        await pilot.pause()
        assert app.screen._columns <= 2


# ---------------------------------------------------------------------------- helper
@pytest.mark.asyncio
async def test_h_on_the_overview_and_f1_in_a_tool_open_the_matching_help():
    app = MysuiteApp()
    async with app.run_test(size=(130, 40)) as pilot:
        await pilot.pause()
        await pilot.press("h")
        await pilot.pause(0.6)
        assert type(app.screen).__name__ == "HelperScreen"
        assert "toolbox" in str(app.screen.query_one("#helper-title", Static).render())
        await pilot.press("h")                                           # h closes it again
        await pilot.pause()
        assert type(app.screen).__name__ == "HomeScreen"

        await pilot.press("1")                                           # Export
        await pilot.pause()
        await pilot.press("f1")
        await pilot.pause(1.5)
        helper = app.screen
        assert type(helper).__name__ == "HelperScreen"
        assert "Export" in str(helper.query_one("#helper-title", Static).render())
        assert "mysuite export" in str(helper.query_one("#helper-cli", Static).render())
        await pilot.press("escape")
        await pilot.pause()
        assert type(app.screen).__name__ == "ExportScreen"               # back where you were


@pytest.mark.asyncio
async def test_every_tool_screen_has_help_and_a_command():
    for spec in TOOL_REGISTRY:
        assert spec.help and spec.cli.startswith("mysuite "), spec.key
        screen = spec.screen_factory()
        assert getattr(screen, "TOOL_KEY", None) == spec.key


@pytest.mark.asyncio
async def test_f1_does_nothing_on_the_welcome_page():
    app = MysuiteApp(show_welcome=True)
    async with app.run_test(size=(110, 36)) as pilot:
        await pilot.pause(0.3)
        app.action_helper()
        await pilot.pause()
        assert type(app.screen).__name__ == "WelcomeScreen"
