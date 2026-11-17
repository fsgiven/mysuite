"""knockout (letters punched out of the background shape) and cutout (background shape removed), plus
variant specs that combine steps: `negativ_dark=knockout+mono:#1D1D1B`.

Built for brand logo sets like BILD's: one positive SVG (red square, white letters) must give the
negative (one square, letters as holes) and the cut-out (letters only), in any colour.
"""
from __future__ import annotations

import io
import shutil
import subprocess

import pytest
from PIL import Image

from mysuite.color import variants as V
from mysuite.color.knockout import cutout, knockout
from tests.helpers import cli, pdf_cmyk_operators

need = pytest.mark.skipif(not all(shutil.which(t) for t in ("rsvg-convert", "gs", "magick")), reason="needs rsvg/gs/magick")

# a square, a bar and an "O" with a counter (evenodd ring), all paths: the BILD structure in miniature
LOGO = ('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100" fill="none">'
        '<g clip-path="url(#c)">'
        '<path d="M0 0H100V100H0Z" fill="#DD0000"/>'
        '<path d="M10 10H30V90H10Z" fill="white"/>'
        '<path fill-rule="evenodd" d="M50 10H90V90H50Z M60 20V80H80V20Z" fill="white"/>'
        '</g><defs><clipPath id="c"><rect width="100" height="100" fill="white"/></clipPath></defs></svg>')

BG, BAR, RING, COUNTER = (3, 3), (20, 50), (55, 50), (70, 50)   # sample points in viewBox units


def render(svg: str, size: int = 200) -> Image.Image:
    png = subprocess.run(["rsvg-convert", "-w", str(size), "-h", str(size)], input=svg.encode(), capture_output=True, check=True).stdout
    return Image.open(io.BytesIO(png)).convert("RGBA")


def at(img: Image.Image, xy) -> tuple[int, int, int, int]:
    s = img.width / 100
    return img.getpixel((int(xy[0] * s), int(xy[1] * s)))


# --------------------------------------------------------------------------- specs
@pytest.mark.parametrize("text,label,steps", [
    ("mono-white", "mono-white", ["mono-white"]),
    ("knockout+mono:#1D1D1B", "knockout+mono-1d1d1b", ["knockout", "mono:#1d1d1b"]),
    ("negativ_dark=knockout+mono:#1D1D1B", "negativ_dark", ["knockout", "mono:#1d1d1b"]),
    ("cut_out=cutout", "cut_out", ["cutout"]),
    ("default", "default", []),
    ("positiv=default", "positiv", []),
])
def test_variant_specs(text, label, steps):
    spec = V.parse_spec(text)
    assert (spec.label, spec.steps) == (label, steps)


@pytest.mark.parametrize("text", ["sparkle", "knockout+", "mono:notacolour", "a/b=cutout", "=cutout", "knockout+knockout"])
def test_bad_variant_specs(text):
    with pytest.raises(V.VariantError):
        V.parse_spec(text)


# ------------------------------------------------------------------------ geometry
@need
def test_knockout_punches_the_shapes_out_of_the_background_as_one_evenodd_path():
    out, notes = knockout(LOGO)
    assert out.count("<path") == 1 and 'fill-rule="evenodd"' in out and "#DD0000" in out
    img = render(out)
    assert at(img, BG)[3] == 255 and at(img, BG)[:3] == (221, 0, 0)
    assert at(img, BAR)[3] == 0 and at(img, RING)[3] == 0          # letters are holes
    assert at(img, COUNTER)[3] == 255                               # the O's counter is background again
    assert not any("mask" in n for n in notes)


@need
def test_knockout_falls_back_to_a_mask_when_paths_cannot_be_merged():
    shifted = LOGO.replace('<path d="M10 10H30V90H10Z" fill="white"/>',
                           '<path d="M0 0H20V80H0Z" transform="translate(10 10)" fill="white"/>')
    out, notes = knockout(shifted)
    assert any("mask" in n for n in notes)
    img = render(out)
    assert at(img, BG)[3] == 255 and at(img, BAR)[3] == 0 and at(img, RING)[3] == 0 and at(img, COUNTER)[3] == 255


@need
def test_cutout_removes_only_the_background():
    img = render(cutout(LOGO)[0])
    assert at(img, BG)[3] == 0 and at(img, COUNTER)[3] == 0
    assert at(img, BAR)[:4] == (255, 255, 255, 255) and at(img, RING)[3] == 255


@need
def test_no_background_shape_is_a_clear_error():
    side_by_side = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 50">'
                    '<rect width="50" height="50" fill="red"/><rect x="50" width="50" height="50" fill="blue"/></svg>')
    with pytest.raises(V.VariantError, match="background"):
        knockout(side_by_side)
    with pytest.raises(V.VariantError):
        knockout('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10"/></svg>')


# ------------------------------------------------------------------------ end to end
@need
def test_export_builds_a_named_brand_set_in_one_run(tmp_path):
    svg = tmp_path / "logo.svg"
    svg.write_text(LOGO)
    res = cli("export", svg, "--formats", "png", "--sizes", "100", "--out", tmp_path / "o", "-q",
              "--variants", "default,negativ_bright=knockout+mono:#fff,negativ_dark=knockout+mono:#1D1D1B,"
                            "cut_out_dark=cutout+mono:#1D1D1B")
    assert res.exit_code == 0, res.stdout
    base = tmp_path / "o" / "logo"
    neg = Image.open(base / "negativ_dark" / "png" / "rgb" / "logo_negativ_dark_100.png").convert("RGBA")
    assert at(neg, BG) == (29, 29, 27, 255) and at(neg, BAR)[3] == 0 and at(neg, COUNTER)[3] == 255
    bright = Image.open(base / "negativ_bright" / "png" / "rgb" / "logo_negativ_bright_100.png").convert("RGBA")
    assert at(bright, BG) == (255, 255, 255, 255) and at(bright, RING)[3] == 0
    cut = Image.open(base / "cut_out_dark" / "png" / "rgb" / "logo_cut_out_dark_100.png").convert("RGBA")
    assert at(cut, BG)[3] == 0 and at(cut, BAR) == (29, 29, 27, 255)
    assert (base / "png" / "rgb" / "logo_100.png").exists()


@need
def test_knockout_stays_flat_vector_cmyk_with_brand_values(tmp_path):
    svg = tmp_path / "logo.svg"
    svg.write_text(LOGO)
    res = cli("export", svg, "--formats", "pdf", "--profiles", "cmyk", "--sizes", "100", "--out", tmp_path / "o", "-q",
              "--variants", "neg=knockout+mono:#1D1D1B", "--cmyk-map", "#1D1D1B=75/65/65/80")
    assert res.exit_code == 0, res.stdout
    inks = pdf_cmyk_operators(next((tmp_path / "o").rglob("*.pdf")))
    assert inks and all(abs(a - b) <= 0.6 for v in inks for a, b in zip(v, (75, 65, 65, 80))), inks


def test_unknown_variant_in_export_is_rejected(tmp_path):
    svg = tmp_path / "logo.svg"
    svg.write_text(LOGO)
    res = cli("export", svg, "--formats", "png", "--out", tmp_path / "o", "--variants", "default,sparkle", "-q")
    assert res.exit_code != 0 and "sparkle" in res.stdout


@need
def test_variants_make_writes_named_svgs(tmp_path):
    svg = tmp_path / "logo.svg"
    svg.write_text(LOGO)
    res = cli("variants", "make", svg, "--variants", "negativ=knockout+mono:#fff,cut=cutout", "--out", tmp_path / "v")
    assert res.exit_code == 0, res.stdout
    names = sorted(p.name for p in (tmp_path / "v").glob("*.svg"))
    assert names == ["logo_cut.svg", "logo_negativ.svg"], names


@need
async def test_dashboard_custom_variants_field_becomes_the_flag(tmp_path):
    import shlex

    from textual.widgets import Checkbox, Input

    from mysuite.tui.app import MysuiteApp
    from mysuite.tui.shell import ChipGroup
    from mysuite.tui.widgets.command_line import CommandLine
    from tests.test_tui_shell import open_export

    svg = tmp_path / "logo.svg"
    svg.write_text(LOGO)
    async with MysuiteApp().run_test() as pilot:
        sc = await open_export(pilot)
        sc.query_one("#input-svg", Input).value = str(svg)
        sc.query_one("#out-dir", Input).value = str(tmp_path / "out")
        sc.query_one("#formats", ChipGroup).select_only(["png"])
        sc.query_one("#variant-specs", Input).value = "negativ_dark=knockout+mono:#1D1D1B cut_out=cutout"
        sc.query_one("#dry-run", Checkbox).value = True
        await pilot.pause()
        await pilot.pause(0.5)
        command = sc.query_one(CommandLine).command
    parts = shlex.split(command)[1:]
    assert parts[parts.index("--variants") + 1] == "default,negativ_dark=knockout+mono:#1D1D1B,cut_out=cutout"
    assert cli(*parts, "--json").exit_code == 0
