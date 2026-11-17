"""Export screen (WS-A item 5): no CMYK mode the CLI rejects, no duplicate variant chip, and fields for
--size-by, --vector-size, --cmyk-profile, --negative-map and the naming options."""
from __future__ import annotations

import json
import shutil

import pytest
from textual.widgets import Checkbox, Input, Select

from mysuite.color.cmyk import CmykSettings, find_default_profile
from mysuite.tui.app import MysuiteApp
from mysuite.tui.shell import ChipGroup, FormError
from tests.helpers import SIMPLE_SVG, cli

pytestmark = pytest.mark.slow
need_tools = pytest.mark.skipif(any(shutil.which(t) is None for t in ("rsvg-convert", "gs", "magick")), reason="needs tools")


async def open_export(pilot):
    await pilot.pause()
    pilot.app.screen._open_tool("export")
    await pilot.pause()
    await pilot.pause()
    return pilot.app.screen


async def test_every_cmyk_mode_on_the_screen_is_one_the_cli_accepts():
    async with MysuiteApp().run_test() as pilot:
        sc = await open_export(pilot)
        modes = [value for _, value in sc.query_one("#cmyk-mode", Select)._options if isinstance(value, str)]
    assert modes and "tokens" not in modes
    for mode in modes:
        CmykSettings.parse(mode)                       # raises for a mode the CLI rejects


async def test_on_dark_is_not_a_second_negative_chip():
    async with MysuiteApp().run_test() as pilot:
        sc = await open_export(pilot)
        chips = sc.query_one("#variants", ChipGroup).options
    assert "negative" in chips and "on-dark" not in chips and len(chips) == len(set(chips))


async def test_size_by_select_offers_the_cli_values():
    from mysuite.export.units import SIZE_BY

    async with MysuiteApp().run_test() as pilot:
        sc = await open_export(pilot)
        values = [value for _, value in sc.query_one("#size-by", Select)._options if isinstance(value, str)]
    assert values == list(SIZE_BY)


@need_tools
async def test_new_fields_become_cli_flags_the_cli_accepts(tmp_path):
    svg = tmp_path / "logo.svg"
    svg.write_text(SIMPLE_SVG)
    profile = find_default_profile()
    async with MysuiteApp().run_test() as pilot:
        sc = await open_export(pilot)
        sc.query_one("#input-svg", Input).value = str(svg)
        sc.query_one("#sizes", Input).value = "512"
        sc.query_one("#out-dir", Input).value = str(tmp_path / "out")
        sc.query_one("#formats", ChipGroup).select_only(["png", "pdf"])
        sc.query_one("#profiles", ChipGroup).select_only(["rgb", "cmyk"])
        sc.query_one("#size-by", Select).value = "height"
        sc.query_one("#vector-size", Input).value = "artwork,100mm"
        if profile:
            sc.query_one("#cmyk-profile", Input).value = str(profile)
        sc.query_one("#variants", ChipGroup).select_only(["default"])
        sc.query_one("#variant-specs", Input).value = "positive=default"
        sc.query_one("#naming", Input).value = "20250514_BILD_Logo_{variant}_{size}_{COLORSPACE}{ext}"
        sc.query_one("#path", Input).value = "{COLORSPACE}/{FORMAT}/{naming}"
        sc.query_one("#name-folder", Checkbox).value = False
        await pilot.pause()
        argv = sc.argv()
    for flag in ("--size-by", "--vector-size", "--naming", "--path", "--no-name-folder"):
        assert flag in argv, (flag, argv)
    assert argv[argv.index("--size-by") + 1] == "height"
    if profile:
        assert argv[argv.index("--cmyk-profile") + 1] == str(profile)
    res = cli("export", *argv, "--dry-run", "--json")
    assert res.exit_code == 0, res.stdout
    outs = sorted(i["output"].split("/out/")[1] for i in json.loads(res.stdout)["items"])
    assert "RGB/PNG/20250514_BILD_Logo_positive_512_RGB.png" in outs
    assert "CMYK/PDF/20250514_BILD_Logo_positive_CMYK.pdf" in outs
    assert "CMYK/PDF/20250514_BILD_Logo_positive_100mm_CMYK.pdf" in outs


async def test_negative_map_field_is_passed_per_pair(tmp_path):
    svg = tmp_path / "logo.svg"
    svg.write_text(SIMPLE_SVG)
    async with MysuiteApp().run_test() as pilot:
        sc = await open_export(pilot)
        sc.query_one("#input-svg", Input).value = str(svg)
        sc.query_one("#negative-map", Input).value = "#1d1d1b=token:--headline-text-color  #dd0000=token:--red"
        await pilot.pause()
        argv = sc.argv()
    pairs = [argv[i + 1] for i, a in enumerate(argv) if a == "--negative-map"]
    assert pairs == ["#1d1d1b=token:--headline-text-color", "#dd0000=token:--red"]


async def test_an_unknown_naming_token_is_flagged_on_the_field(tmp_path):
    svg = tmp_path / "logo.svg"
    svg.write_text(SIMPLE_SVG)
    async with MysuiteApp().run_test() as pilot:
        sc = await open_export(pilot)
        sc.query_one("#input-svg", Input).value = str(svg)
        sc.query_one("#naming", Input).value = "{name}_{colour}{ext}"
        await pilot.pause()
        with pytest.raises(FormError) as err:
            sc.argv()
    assert err.value.widget_id == "naming" and "colour" in str(err.value)
