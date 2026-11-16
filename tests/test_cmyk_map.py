"""Brand CMYK values: --cmyk-map '#DD0000=0/100/100/0' pins a colour's inks instead of computing them.

A brand book fixes a colour's print values (BILD red is 0/100/100/0, its dark grey 75/65/65/80); a colour
profile can only approximate them (SWOP gives 6.3/100/100/1.2 for #DD0000). Pinned values must reach
PDF, EPS and TIFF unchanged, survive clean mode, and beat the greys-are-K-only rule.
"""
from __future__ import annotations

import json
import shutil

import pytest

from mysuite.color.cmyk import CmykEngine, CmykError, CmykSettings, parse_cmyk_map
from tests.helpers import cli, pdf_cmyk_operators, tiff_cmyk_pixel, write_svg

need = pytest.mark.skipif(not all(shutil.which(t) for t in ("rsvg-convert", "gs", "magick")), reason="needs rsvg/gs/magick")

BILD = ["#DD0000=0/100/100/0", "#1D1D1B=75/65/65/80"]


# ------------------------------------------------------------------ parsing
@pytest.mark.parametrize("text,rgb,cmyk", [
    ("#DD0000=0/100/100/0", (221, 0, 0), (0, 100, 100, 0)),
    ("#d00=0/100/100/0", (221, 0, 0), (0, 100, 100, 0)),
    ("red=0,100,100,0", (255, 0, 0), (0, 100, 100, 0)),
    ("#1d1d1b = 75 65 65 80", (29, 29, 27), (75, 65, 65, 80)),
    ("#123456=12.5/0/0/3", (18, 52, 86), (12.5, 0, 0, 3)),
])
def test_parse_accepts_hex_names_and_separators(text, rgb, cmyk):
    assert parse_cmyk_map([text]) == {rgb: cmyk}


def test_parse_takes_several_pairs_and_a_dict():
    assert parse_cmyk_map(["#DD0000=0/100/100/0 #1D1D1B=75/65/65/80"]) == {
        (221, 0, 0): (0, 100, 100, 0), (29, 29, 27): (75, 65, 65, 80)}
    assert parse_cmyk_map({"#DD0000": "0/100/100/0"}) == {(221, 0, 0): (0, 100, 100, 0)}


@pytest.mark.parametrize("text", ["#DD0000", "#DD0000=0/100/100", "#DD0000=0/100/100/120", "#DD0000=a/b/c/d",
                                  "nocolour=0/0/0/0", "#DD0000=0/100/100/0/5"])
def test_parse_rejects_bad_pairs(text):
    with pytest.raises(CmykError):
        parse_cmyk_map([text])


# ------------------------------------------------------------------- engine
def test_mapped_colour_keeps_its_inks_and_is_marked():
    engine = CmykEngine(CmykSettings.parse("exact", cmyk_map=parse_cmyk_map(BILD)))
    red = engine.convert(221, 0, 0)
    assert red.cmyk == (0, 100, 100, 0) and red.fixed
    assert "brand" in red.label()
    assert not engine.convert(0, 87, 184).fixed            # unmapped colours still use the profile


def test_anti_aliased_near_colours_take_the_brand_value_but_distinct_ones_do_not():
    engine = CmykEngine(CmykSettings.parse("exact", cmyk_map=parse_cmyk_map(BILD)))
    assert engine.convert(221, 1, 1).cmyk == (0, 100, 100, 0)       # edge pixel of the red
    assert engine.convert(30, 30, 28).cmyk == (75, 65, 65, 80)      # edge pixel of the dark grey
    assert engine.convert(200, 0, 0).cmyk != (0, 100, 100, 0)       # a visibly different red is computed
    assert engine.convert(238, 110, 110).cmyk != (0, 100, 100, 0)   # a red/white edge blend is computed


def test_clean_mode_does_not_round_a_mapped_colour():
    engine = CmykEngine(CmykSettings.parse("clean:10", cmyk_map=parse_cmyk_map(["#0057B8=97/62/0/3"])))
    assert engine.convert(0, 87, 184).cmyk == (97, 62, 0, 3)


def test_mapped_grey_beats_the_black_only_rule():
    engine = CmykEngine(CmykSettings.parse("exact", cmyk_map=parse_cmyk_map(["#222222=70/60/60/90"])))
    assert engine.convert(34, 34, 34).cmyk == (70, 60, 60, 90)
    assert engine.convert(68, 68, 68).cmyk[:3] == (0, 0, 0)  # other greys unchanged


# ------------------------------------------------------------- end to end
SWATCHES = '<rect width="100" height="100" fill="#DD0000"/><rect x="100" width="100" height="100" fill="#1D1D1B"/>'


def _export(tmp_path, *args):
    svg = write_svg(tmp_path / "s.svg", SWATCHES)
    res = cli("export", svg, "--formats", "pdf,eps,tiff", "--profiles", "cmyk", "--sizes", "200",
              "--out", tmp_path / "o", *args, "-q")
    assert res.exit_code == 0, res.stdout
    return {p.suffix: p for p in (tmp_path / "o").rglob("*") if p.is_file()}


def _close(a, b, tol=0.6):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


@need
@pytest.mark.parametrize("mode", ["exact", "clean"])
def test_brand_inks_land_in_pdf_eps_and_tiff(tmp_path, mode):
    files = _export(tmp_path, "--cmyk-mode", mode, "--cmyk-map", BILD[0], "--cmyk-map", BILD[1])
    pdf = pdf_cmyk_operators(files[".pdf"])
    assert any(_close(v, (0, 100, 100, 0)) for v in pdf), pdf
    assert any(_close(v, (75, 65, 65, 80)) for v in pdf), pdf
    from tests.test_cmyk_engine import eps_inks
    eps = eps_inks(files[".eps"])
    assert any(_close(v, (0, 100, 100, 0)) for v in eps) and any(_close(v, (75, 65, 65, 80)) for v in eps), eps
    for xy, want in (((50, 50), (0, 100, 100, 0)), ((150, 50), (75, 65, 65, 80))):
        got = tuple(round(v / 255 * 100, 1) for v in tiff_cmyk_pixel(files[".tiff"], *xy))
        assert _close(got, want, 0.5), (got, want)


@need
def test_json_reports_which_colours_were_pinned(tmp_path):
    svg = write_svg(tmp_path / "s.svg", SWATCHES)
    res = cli("export", svg, "--formats", "pdf", "--profiles", "cmyk", "--sizes", "100", "--out", tmp_path / "o",
              "--cmyk-map", BILD[0], "--json")
    assert res.exit_code == 0, res.stdout
    colours = {c["rgb"]: c for c in json.loads(res.stdout)["cmyk"]["colours"]}
    assert colours["#dd0000"]["fixed"] is True and colours["#dd0000"]["cmyk"] == [0, 100, 100, 0]
    assert colours["#1d1d1b"]["fixed"] is False


def test_bad_cmyk_map_is_a_clean_cli_error(tmp_path):
    svg = write_svg(tmp_path / "s.svg", SWATCHES)
    res = cli("export", svg, "--formats", "pdf", "--profiles", "cmyk", "--out", tmp_path / "o",
              "--cmyk-map", "#DD0000=0/100/100", "-q")
    assert res.exit_code != 0 and "cmyk-map" in res.stdout.lower()


@need
def test_cmyk_map_from_a_profile_in_mysuite_toml(tmp_path):
    cfg = tmp_path / "mysuite.toml"
    cfg.write_text('[profiles.bild.export]\ncmyk_map = { "#DD0000" = "0/100/100/0" }\n')
    svg = write_svg(tmp_path / "s.svg", SWATCHES)
    res = cli("--profile", "bild", "export", svg, "--formats", "pdf", "--profiles", "cmyk", "--sizes", "100",
              "--out", tmp_path / "o", "--config", cfg, "-q")
    assert res.exit_code == 0, res.stdout
    pdf = pdf_cmyk_operators(next((tmp_path / "o").rglob("*.pdf")))
    assert any(_close(v, (0, 100, 100, 0)) for v in pdf), pdf


# ---------------------------------------------------------------------------- dashboard
@need
async def test_dashboard_brand_cmyk_field_becomes_the_flag(tmp_path):
    import shlex

    from textual.widgets import Checkbox, Input

    from mysuite.tui.app import MysuiteApp
    from mysuite.tui.shell import ChipGroup
    from mysuite.tui.widgets.command_line import CommandLine
    from tests.test_tui_shell import open_export

    svg = write_svg(tmp_path / "logo.svg", SWATCHES)
    async with MysuiteApp().run_test() as pilot:
        sc = await open_export(pilot)
        sc.query_one("#input-svg", Input).value = str(svg)
        sc.query_one("#out-dir", Input).value = str(tmp_path / "out")
        sc.query_one("#formats", ChipGroup).select_only(["pdf"])
        sc.query_one("#profiles", ChipGroup).select_only(["cmyk"])
        sc.query_one("#cmyk-map", Input).value = "#DD0000=0/100/100/0 #1D1D1B=75/65/65/80"
        sc.query_one("#dry-run", Checkbox).value = True
        await pilot.pause()
        await pilot.pause(0.5)
        command = sc.query_one(CommandLine).command
    parts = shlex.split(command)[1:]
    assert parts[parts.index("--cmyk-map") + 1] == "#DD0000=0/100/100/0 #1D1D1B=75/65/65/80"
    assert cli(*parts, "--json").exit_code == 0
