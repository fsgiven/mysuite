from __future__ import annotations

import json

import numpy as np
import pytest
from PIL import Image, ImageDraw

from mysuite.cli import app
from mysuite.relight import relight as R
from tests.helpers import runner


def disc(path, size=300, colour=(180, 180, 180)):
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(im).ellipse([40, 40, size - 40, size - 40], fill=(*colour, 255))
    im.save(path)
    return path


def run_r(*args):
    res = runner.invoke(app, ["relight", *[str(a) for a in args], "--json"])
    return res, json.loads(res.stdout)


def px(path, x, y):
    return np.asarray(Image.open(path).convert("RGB"), dtype=float)[y, x].mean()


@pytest.mark.parametrize("direction,brighter,darker", [
    ("left", (70, 150), (230, 150)),
    ("right", (230, 150), (70, 150)),
    ("top", (150, 70), (150, 230)),
    ("bottom", (150, 230), (150, 70)),
    ("top-left", (80, 80), (220, 220)),
])
def test_the_side_facing_the_light_is_brighter(tmp_path, direction, brighter, darker):
    src = disc(tmp_path / "d.png")
    res, doc = run_r(src, "--direction", direction, "--height", "35")
    out = doc["items"][0]["output"]
    assert px(out, *brighter) > px(src, *brighter) + 15 and px(out, *darker) < px(src, *darker) - 15


def test_angle_matches_the_named_direction_and_beats_it(tmp_path):
    src = disc(tmp_path / "d.png")
    _, a = run_r(src, "--direction", "left")
    left = Image.open(a["items"][0]["output"]).copy()
    _, b = run_r(src, "--angle", "270", "--direction", "right", "--overwrite")     # angle wins over direction
    assert np.abs(np.asarray(left, float) - np.asarray(Image.open(b["items"][0]["output"]), float)).max() < 1.5


def test_flat_opaque_image_keeps_its_brightness_where_nothing_has_depth(tmp_path):
    flat = Image.new("RGB", (120, 120), (100, 140, 200))
    p = tmp_path / "f.png"
    flat.save(p)
    _, doc = run_r(p)
    a = np.asarray(Image.open(doc["items"][0]["output"]), float)
    assert np.abs(a[30:90, 30:90] - np.array([100, 140, 200])).max() <= 2        # flat -> unchanged


def test_alpha_is_preserved_and_transparent_pixels_stay_transparent(tmp_path):
    src = disc(tmp_path / "d.png")
    _, doc = run_r(src)
    out = Image.open(doc["items"][0]["output"])
    assert out.mode == "RGBA" and out.getpixel((2, 2))[3] == 0 and out.getpixel((150, 150))[3] == 255


def test_colour_tints_the_lit_side_and_specular_adds_a_highlight(tmp_path):
    src = disc(tmp_path / "d.png")
    _, plain = run_r(src, "--direction", "left")
    _, warm = run_r(src, "--direction", "left", "--color", "#ff8800", "--overwrite")
    w = Image.open(warm["items"][0]["output"]).convert("RGB").getpixel((70, 150))
    assert w[0] > w[2] + 40                                                      # warm: red well above blue
    _, shiny = run_r(src, "--direction", "left", "--specular", "1", "--overwrite")
    dim = disc(tmp_path / "dim.png", colour=(90, 90, 90))                         # dark base so nothing clips to white
    total = lambda d: np.asarray(Image.open(d["items"][0]["output"]).convert("RGB"), float).sum()
    matte = total(run_r(dim, "--direction", "left", "--intensity", "0.5")[1])      # both runs write dim_relit.png
    sheen = total(run_r(dim, "--direction", "left", "--intensity", "0.5", "--specular", "1", "--overwrite")[1])
    assert sheen > matte                                                           # the sheen adds light


def test_presets_exist_and_explicit_flags_override_them(tmp_path):
    for name in R.PRESETS:
        R.RelightSettings.from_options(name, {}).validate()
    s = R.RelightSettings.from_options("dramatic", {"intensity": 0.5, "angle": 90})
    assert s.intensity == 0.5 and s.angle == 90 and s.direction is None and s.ambient == R.PRESETS["dramatic"]["ambient"]
    src = disc(tmp_path / "d.png")
    res, doc = run_r(src, "--preset", "golden-hour")
    assert res.exit_code == 0 and doc["settings"]["color"] == "#ffb36b"


@pytest.mark.parametrize("args,fragment", [
    (["--direction", "up"], "direction"), (["--preset", "disco"], "preset"), (["--height", "0"], "height"),
    (["--intensity", "9"], "intensity"), (["--softness", "99"], "softness"), (["--color", "zz"], "color"),
    (["--specular", "5"], "specular"), (["--depth", "-1"], "depth"),
])
def test_bad_settings_are_clean_errors(tmp_path, args, fragment):
    src = disc(tmp_path / "d.png")
    res, doc = run_r(src, *args)
    assert res.exit_code == 1 and fragment in doc["errors"][0] and not list(tmp_path.glob("*_relit*"))


def test_never_overwrites_and_original_untouched_and_dry_run(tmp_path):
    src = disc(tmp_path / "d.png")
    before = src.read_bytes()
    _, doc = run_r(src, "--dry-run")
    assert doc["items"][0]["status"] == "planned" and not (tmp_path / "d_relit.png").exists()
    run_r(src)
    first = (tmp_path / "d_relit.png").read_bytes()
    _, doc = run_r(src, "--angle", "90")
    assert doc["items"][0]["status"] == "skipped_existing" and (tmp_path / "d_relit.png").read_bytes() == first
    assert src.read_bytes() == before


def test_vector_and_corrupt_inputs(tmp_path):
    svg = tmp_path / "x.svg"
    svg.write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    res, doc = run_r(svg)
    assert res.exit_code == 1 and "no raster images" in doc["errors"][0]
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"nope")
    res, doc = run_r(bad)
    assert res.exit_code == 1 and doc["items"][0]["status"] == "failed"


def test_sandbox_and_huge_image(tmp_path, monkeypatch):
    jail = tmp_path / "jail"
    jail.mkdir()
    src = disc(tmp_path / "d.png")
    res = runner.invoke(app, ["--allow", str(jail), "relight", str(src), "--json"])
    assert res.exit_code == 3
    monkeypatch.setattr(R, "MAX_PIXELS", 100)
    res, doc = run_r(src)
    assert res.exit_code == 1 and "limit" in doc["items"][0]["error"]


def test_blur_helper_is_an_average():
    a = np.zeros((50, 50))
    a[20:30, 20:30] = 1.0
    b = R._blur(a, 4)
    assert abs(b.sum() - a.sum()) < 1 and b.max() < 1 and b.shape == a.shape
