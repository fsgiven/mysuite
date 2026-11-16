"""`--recolor FROM=transparent` and contact sheets that show transparency and tell repeated names apart."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from mysuite.cli import app
from mysuite.color.svg import recolor_text
from mysuite.export.recolor import InvalidRecolorError, parse_recolor_list
from mysuite.inspect.inspect import CHECKER, fit_label, on_checkerboard, sheet_labels
from tests.helpers import runner

need_render = pytest.mark.skipif(
    any(shutil.which(t) is None for t in ("rsvg-convert", "gs")), reason="needs rsvg-convert and gs"
)

RED_ON_BLUE = ('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
               '<defs><linearGradient id="g"><stop offset="0" stop-color="#dd0000"/><stop offset="1" stop-color="#dd0000"/>'
               '</linearGradient></defs><rect width="100" height="100" fill="#0000ff"/>'
               '<rect x="25" y="25" width="50" height="50" style="fill:#dd0000;stroke:#dd0000"/>'
               '<rect width="20" height="20" fill="url(#g)"/></svg>')
WHITE_LOGO = ('<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100">'
              '<rect x="10" y="10" width="80" height="80" fill="#ffffff"/><rect x="110" y="30" width="80" height="20" fill="#ffffff"/></svg>')


def colours(im):
    return {c for _, c in im.convert("RGB").getcolors(1 << 24)}


def run(*args):
    res = runner.invoke(app, [str(a) for a in args] + ["--json"])
    return res, json.loads(res.stdout)


# ---------------------------------------------------------------------------- transparent as a target
@pytest.mark.parametrize("word", ["transparent", "none", "TRANSPARENT", "None"])
def test_transparent_and_none_are_accepted_as_a_target(word):
    assert parse_recolor_list([f"#dd0000={word}"]) == {"#dd0000": "transparent"}


@pytest.mark.parametrize("word", ["transparent", "none"])
def test_transparent_is_refused_as_a_source_with_a_clear_message(word):
    with pytest.raises(InvalidRecolorError, match="only be the TO side.*#dd0000=transparent"):
        parse_recolor_list([f"{word}=#ffffff"])


def test_transparent_target_is_none_for_fill_and_stroke_and_transparent_for_stops():
    out = recolor_text(RED_ON_BLUE, {"#dd0000": "transparent"}, 0)
    assert "fill:none;stroke:none" in out                         # the SVG 1.1 keyword every tool knows
    assert out.count('stop-color="transparent"') == 2              # stop-color="none" is invalid -> black
    assert 'fill="#0000ff"' in out and "#dd0000" not in out


@need_render
def test_transparent_recolor_is_transparent_in_png_and_not_black_in_pdf_or_eps(tmp_path):
    logo = tmp_path / "logo.svg"
    logo.write_text(RED_ON_BLUE)
    res, doc = run("export", logo, "--formats", "png,pdf,eps", "--profiles", "rgb,cmyk", "--cmyk-mode", "clean",
                   "--sizes", "100", "--recolor", "#dd0000=transparent", "--out", tmp_path / "o")
    assert res.exit_code == 0, res.stdout
    outputs = [Path(i["output"]) for i in doc["items"] if i["status"] == "written"]
    assert {p.suffix for p in outputs} == {".png", ".pdf", ".eps"}
    for out in outputs:
        png = out if out.suffix == ".png" else tmp_path / f"{out.stem}_{out.suffix[1:]}_{out.parent.name}.png"
        if out.suffix != ".png":
            subprocess.run(["gs", "-q", "-dNOPAUSE", "-dBATCH", "-dSAFER", "-dEPSCrop", "-sDEVICE=png16m", "-r72",
                            f"-sOutputFile={png}", str(out)], check=True)
        with Image.open(png) as im:
            im = im.convert("RGB")
            for xy in ((im.width // 2, im.height // 2), (im.width // 20, im.height // 20)):   # square and gradient
                r, g, b = im.getpixel(xy)
                assert b > 120 and r < 90, f"{out}: {xy} is {(r, g, b)}, not the blue underneath"


@need_render
def test_transparent_recolor_leaves_alpha_zero_in_png(tmp_path):
    logo = tmp_path / "logo.svg"
    logo.write_text(WHITE_LOGO)
    res, doc = run("export", logo, "--formats", "png", "--sizes", "200", "--recolor", "white=none", "--out", tmp_path / "o")
    assert res.exit_code == 0, res.stdout
    with Image.open(doc["items"][0]["output"]) as im:
        assert im.convert("RGBA").getchannel("A").getextrema() == (0, 0)


def test_transparent_as_source_fails_through_the_cli(tmp_path):
    logo = tmp_path / "logo.svg"
    logo.write_text(WHITE_LOGO)
    res, doc = run("export", logo, "--formats", "png", "--recolor", "transparent=#fff", "--out", tmp_path / "o")
    assert res.exit_code != 0 and "TO side" in doc["errors"][0] and not (tmp_path / "o").exists()


# ---------------------------------------------------------------------------------- contact sheets
def test_checkerboard_only_under_transparency():
    opaque = Image.new("RGBA", (40, 40), (10, 20, 30, 255))
    assert colours(on_checkerboard(opaque)) == {(10, 20, 30)}
    clear = on_checkerboard(Image.new("RGBA", (40, 40), (255, 255, 255, 0)))
    assert colours(clear) == set(CHECKER)
    half = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
    half.paste((255, 255, 255, 255), (0, 0, 20, 40))
    out = on_checkerboard(half)
    assert out.getpixel((5, 5)) == (255, 255, 255) and out.getpixel((30, 5)) in CHECKER


def test_labels_are_the_name_unless_names_repeat(tmp_path):
    a, b = tmp_path / "x" / "a.png", tmp_path / "y" / "b.png"
    assert sheet_labels([a, b]) == ["a.png", "b.png"]
    assert sheet_labels([tmp_path / "x" / "l.png", tmp_path / "y" / "l.png"]) == ["x/l.png", "y/l.png"]


def test_labels_pick_the_shortest_distinguishing_path():
    paths = [Path(f"/w/out/logo/{v}/png/rgb/logo_512.png") for v in ("invert", "mono-white")]
    paths += [Path("/w/out/logo/png/rgb/logo_512.png"), Path("/w/out/other.png"), Path("/w/out/other.png")]
    assert sheet_labels(paths) == ["invert/…/logo_512.png", "mono-white/…/logo_512.png", "logo/…/logo_512.png",
                                   "other.png", "other.png"]          # the very same file twice keeps its name


def test_fit_label_keeps_both_ends():
    from PIL import ImageFont

    font = ImageFont.load_default()
    text = "12. a-very-long-variant-folder-name/…/logo_512.png"
    short = fit_label(text, font, 120)
    assert font.getlength(short) <= 120 and short.startswith("12. a") and short.endswith(".png")
    assert fit_label("1. x.png", font, 200) == "1. x.png"


@need_render
@pytest.mark.parametrize("command", ["sheet", "inspect"])
def test_white_on_transparent_logo_is_visible_on_the_sheet(tmp_path, command):
    logo = tmp_path / "logo.svg"
    logo.write_text(WHITE_LOGO)
    _, doc = run("export", logo, "--formats", "png", "--sizes", "200", "--variants", "default,invert", "--out", tmp_path / "o")
    pngs = sorted(Path(i["output"]) for i in doc["items"])
    assert len(pngs) == 2 and pngs[0].name == pngs[1].name
    sheet = tmp_path / "s.png"
    if command == "sheet":
        res, _ = run("sheet", *pngs, "--out", sheet, "--columns", "2", "--cell", "200", "--background", "#ffffff")
    else:
        res, _ = run("inspect", *pngs, "--sheet", sheet)
    assert res.exit_code == 0, res.stdout
    with Image.open(sheet) as im:
        seen = colours(im)
    assert set(CHECKER) <= seen                     # the transparent area is a checkerboard, not white
    assert (255, 255, 255) in seen and (0, 0, 0) in seen


@need_render
def test_sheet_of_an_opaque_picture_is_unchanged(tmp_path):
    pic = tmp_path / "p.png"
    Image.new("RGB", (100, 100), (200, 60, 40)).save(pic)
    run("sheet", pic, "--out", tmp_path / "s.png", "--cell", "100", "--no-labels")
    with Image.open(tmp_path / "s.png") as im:
        assert im.convert("RGB").crop((12, 12, 112, 112)).getcolors() == [(10000, (200, 60, 40))]
