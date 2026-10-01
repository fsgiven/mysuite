from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from mysuite.cli import app
from mysuite.specs import core
from tests.helpers import SIMPLE_SVG, runner

P3 = Path("/System/Library/ColorSync/Profiles/Display P3.icc")


def run_cmd(*args):
    res = runner.invoke(app, [str(a) for a in args] + ["--json"])
    return res, json.loads(res.stdout)


def photo(path, size=(400, 300), colour=(200, 60, 40)):
    im = Image.new("RGB", size, colour)
    im.paste((20, 80, 200), (0, 0, size[0] // 2, size[1] // 2))
    im.save(path)
    return path


# ------------------------------------------------------------------------------ print
@pytest.mark.parametrize("size,dpi,expected", [
    ("10x15cm", 300, (1181, 1772)), ("4x6in", 300, (1200, 1800)), ("210x297mm", 300, (2480, 3508)),
    ("1200x630", 72, (1200, 630)), ("1200x630px", 300, (1200, 630)), ("500x500", 300, (500, 500)), ("10x10cm", 150, (591, 591)),
])
def test_size_parsing_is_exact(size, dpi, expected):
    assert core.parse_size(size, dpi)[:2] == expected


@pytest.mark.parametrize("bad", ["10", "10x", "axb", "0x10cm", "10x10yd", "999999x999999", ""])
def test_bad_sizes(bad):
    with pytest.raises(core.SpecError):
        core.parse_size(bad, 300)


def test_print_is_exactly_the_requested_pixels_with_the_dpi_stored(tmp_path):
    src = photo(tmp_path / "p.png")
    res, doc = run_cmd("print", src, "--size", "10x15cm", "--dpi", "300")
    out = Image.open(doc["items"][0]["output"])
    assert out.size == (1181, 1772) and doc["items"][0]["size"] == [1181, 1772]
    assert round(out.info["dpi"][0]) == 300
    res, doc = run_cmd("print", src, "--size", "500x500", "--format", "jpeg", "--overwrite")
    jpg = Image.open(doc["items"][0]["output"])
    assert jpg.size == (500, 500) and jpg.format == "JPEG" and round(jpg.info["dpi"][0]) == 300


def test_fit_modes(tmp_path):
    src = photo(tmp_path / "p.png", (400, 200))                    # 2:1, left-top quadrant blue
    contain = Image.open(run_cmd("print", src, "--size", "200x200", "--fit", "contain", "--background", "#00ff00")[1]["items"][0]["output"]).convert("RGB")
    assert contain.size == (200, 200) and contain.getpixel((100, 5)) == (0, 255, 0) and contain.getpixel((100, 100)) != (0, 255, 0)
    cover = Image.open(run_cmd("print", src, "--size", "200x200", "--fit", "cover", "--overwrite")[1]["items"][0]["output"]).convert("RGB")
    assert cover.size == (200, 200) and cover.getpixel((5, 5)) == (20, 80, 200) and cover.getpixel((195, 195)) == (200, 60, 40)
    left = Image.open(run_cmd("print", src, "--size", "100x200", "--fit", "cover", "--gravity", "left", "--overwrite")[1]["items"][0]["output"]).convert("RGB")
    right = Image.open(run_cmd("print", src, "--size", "100x200", "--fit", "cover", "--gravity", "right", "--overwrite")[1]["items"][0]["output"]).convert("RGB")
    assert left.getpixel((50, 20)) == (20, 80, 200) and right.getpixel((50, 20)) == (200, 60, 40)     # which part is kept
    stretch = Image.open(run_cmd("print", src, "--size", "100x300", "--fit", "stretch", "--overwrite")[1]["items"][0]["output"])
    assert stretch.size == (100, 300)


def test_transparent_stays_transparent_unless_a_background_or_jpeg(tmp_path):
    rgba = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    rgba.paste((255, 0, 0, 255), (25, 25, 75, 75))
    src = tmp_path / "t.png"
    rgba.save(src)
    out = Image.open(run_cmd("print", src, "--size", "200x200")[1]["items"][0]["output"])
    assert out.mode == "RGBA" and out.getpixel((2, 2))[3] == 0
    jpg = Image.open(run_cmd("print", src, "--size", "200x200", "--format", "jpeg")[1]["items"][0]["output"])
    assert jpg.getpixel((2, 2))[0] > 240                                                     # white, not black


def test_bleed_and_auto_rotate(tmp_path):
    src = photo(tmp_path / "p.png", (300, 200))
    res, doc = run_cmd("print", src, "--size", "100x150mm", "--dpi", "254", "--bleed", "3")
    w, h = 1000, 1500
    b = round(3 / 25.4 * 254)
    assert Image.open(doc["items"][0]["output"]).size == (w + 2 * b, h + 2 * b) and any("bleed" in n for n in doc["items"][0]["notes"])
    res, doc = run_cmd("print", src, "--size", "200x400", "--fit", "cover", "--auto-rotate", "--overwrite")
    out = Image.open(doc["items"][0]["output"]).convert("RGB")
    assert any("turned" in n for n in doc["items"][0]["notes"]) and out.size == (200, 400)


@pytest.mark.skipif(shutil.which("rsvg-convert") is None, reason="needs rsvg-convert")
def test_vector_source_is_rendered_at_the_target_size(tmp_path):
    svg = tmp_path / "logo.svg"
    svg.write_text(SIMPLE_SVG)
    res, doc = run_cmd("print", svg, "--size", "1000x500", "--fit", "cover")
    out = Image.open(doc["items"][0]["output"]).convert("RGB")
    assert out.size == (1000, 500) and out.getpixel((100, 250)) == (221, 0, 0)               # crisp, not an upscaled bitmap


def test_print_errors_overwrite_dry_run_and_sandbox(tmp_path):
    src = photo(tmp_path / "p.png")
    for args in (["--size", "bad"], ["--size", "10x10cm", "--fit", "zoom"], ["--size", "10x10cm", "--dpi", "5"],
                 ["--size", "10x10cm", "--format", "gif"], ["--size", "10x10cm", "--background", "zzz"], ["--size", "10x10cm", "--gravity", "up"]):
        res, doc = run_cmd("print", src, *args)
        assert res.exit_code == 1, args
    assert not list(tmp_path.glob("*_print*"))
    res, doc = run_cmd("print", src, "--size", "10x10cm", "--dry-run")
    assert doc["items"][0]["size"] == [1181, 1181] and not list(tmp_path.glob("*_print*"))
    run_cmd("print", src, "--size", "100x100")
    first = (tmp_path / "p_print.png").read_bytes()
    assert run_cmd("print", src, "--size", "200x200")[1]["items"][0]["status"] == "skipped_existing"
    assert (tmp_path / "p_print.png").read_bytes() == first
    jail = tmp_path / "jail"
    jail.mkdir()
    assert runner.invoke(app, ["--allow", str(jail), "print", str(src), "--size", "10x10cm", "--json"]).exit_code == 3


# ------------------------------------------------------------------------------ rename
def files(tmp_path, names):
    out = []
    for i, n in enumerate(names):
        p = tmp_path / n
        photo(p, (40 + i * 10, 30))
        out.append(p)
    return out


def test_pattern_tokens(tmp_path):
    (a,) = files(tmp_path, ["IMG_001.JPG"])
    when = core._capture_time(a)
    assert core.new_name("{name}_{n:3}{ext}", a, 7, when) == "IMG_001_007.jpg"
    assert core.new_name("{date}_{w}x{h}{ext}", a, 1, when) == f"{when:%Y-%m-%d}_40x30.jpg"
    assert core.new_name("{datetime}{ext}", a, 1, when) == f"{when:%Y-%m-%d_%H%M%S}.jpg"


@pytest.mark.parametrize("pattern", ["{nope}", "a/b{ext}", "../x", "{name", "x" * 300])
def test_bad_patterns(tmp_path, pattern):
    (a,) = files(tmp_path, ["a.png"])
    with pytest.raises(core.SpecError):
        core.new_name(pattern, a, 1, core._capture_time(a))


def test_rename_copies_by_default_and_keeps_originals(tmp_path):
    a, b = files(tmp_path, ["b.png", "a.png"])
    res, doc = run_cmd("rename", tmp_path, "--pattern", "trip_{n:2}{ext}", "--out", tmp_path / "out")
    assert [i["output"].rsplit("/", 1)[1] for i in doc["items"]] == ["trip_01.png", "trip_02.png"]        # sorted by name: a then b
    assert a.exists() and b.exists() and (tmp_path / "out" / "trip_01.png").read_bytes() == (tmp_path / "a.png").read_bytes()


def test_rename_move_in_place_and_swap_safety(tmp_path):
    a, b = files(tmp_path, ["a.png", "b.png"])
    ba, bb = a.read_bytes(), b.read_bytes()
    res, doc = run_cmd("rename", a, b, "--pattern", "{n}{ext}", "--start", "2", "--move")
    assert res.exit_code == 0 and not a.exists() and not b.exists()
    assert (tmp_path / "2.png").read_bytes() == ba and (tmp_path / "3.png").read_bytes() == bb
    # a swap: "2.png" -> 3.png and "3.png" -> 2.png must not clobber each other
    res, doc = run_cmd("rename", tmp_path / "2.png", tmp_path / "3.png", "--pattern", "{n}{ext}", "--start", "3", "--sort", "size", "--move")
    assert sorted(p.name for p in tmp_path.glob("*.png")) == ["3.png", "4.png"]


def test_rename_collisions_existing_targets_and_dry_run(tmp_path):
    a, b = files(tmp_path, ["a.png", "b.png"])
    res, doc = run_cmd("rename", a, b, "--pattern", "same{ext}")
    assert res.exit_code == 1 and "both become" in doc["errors"][0] and not (tmp_path / "same.png").exists()
    res, doc = run_cmd("rename", a, "--pattern", "x{ext}", "--dry-run")
    assert doc["items"][0]["status"] == "planned" and not (tmp_path / "x.png").exists()
    (tmp_path / "x.png").write_bytes(b"keep me")
    res, doc = run_cmd("rename", a, "--pattern", "x{ext}")
    assert doc["items"][0]["status"] == "skipped_existing" and (tmp_path / "x.png").read_bytes() == b"keep me"
    res, doc = run_cmd("rename", a, "--pattern", "x{ext}", "--overwrite")
    assert doc["items"][0]["status"] == "written" and (tmp_path / "x.png").read_bytes() == a.read_bytes()


def test_rename_sort_by_date_and_size(tmp_path):
    old, new = files(tmp_path, ["z_old.png", "a_new.png"])
    os.utime(old, (1_000_000_000, 1_000_000_000))
    os.utime(new, (1_700_000_000, 1_700_000_000))
    res, doc = run_cmd("rename", old, new, "--pattern", "{n}{ext}", "--sort", "date", "--dry-run")
    mapping = {i["input"].rsplit("/", 1)[1]: i["output"].rsplit("/", 1)[1] for i in doc["items"]}
    assert mapping == {"z_old.png": "1.png", "a_new.png": "2.png"}
    res, doc = run_cmd("rename", old, new, "--pattern", "{n}{ext}", "--sort", "weight", "--dry-run")
    assert res.exit_code == 1


def test_rename_sandbox_and_hostile_names(tmp_path):
    jail = tmp_path / "jail"
    jail.mkdir()
    (a,) = files(jail, ["a.png"])
    res = runner.invoke(app, ["--allow", str(jail), "rename", str(a), "--pattern", "x{ext}", "--out", str(tmp_path / "out"), "--json"])
    assert res.exit_code == 3 and not (tmp_path / "out").exists()
    weird = tmp_path / "[bold]-all=.png"
    photo(weird)
    res, doc = run_cmd("rename", weird, "--pattern", "ok{ext}")
    assert res.exit_code == 0 and (tmp_path / "ok.png").exists()


# -------------------------------------------------------------------------------- sheet
def test_sheet_size_and_formats(tmp_path):
    imgs = files(tmp_path, [f"i{i}.png" for i in range(6)])
    res, doc = run_cmd("sheet", *imgs, "--out", tmp_path / "s.png", "--columns", "3", "--cell", "100", "--title", "demo")
    sheet = Image.open(tmp_path / "s.png")
    assert sheet.size == (3 * (100 + 12) + 12, 36 + 2 * (100 + 22 + 12) + 12) and doc["items"][0]["images"] == 6
    res, doc = run_cmd("sheet", *imgs, "--out", tmp_path / "s2.png", "--columns", "3", "--cell", "100", "--no-labels")
    assert Image.open(tmp_path / "s2.png").size == (3 * 112 + 12, 2 * (100 + 12) + 12)
    res, doc = run_cmd("sheet", *imgs, "--out", tmp_path / "s.pdf")
    from pypdf import PdfReader

    assert len(PdfReader(str(tmp_path / "s.pdf")).pages) == 1


def test_sheet_background_bad_file_overwrite_and_errors(tmp_path):
    ok, = files(tmp_path, ["ok.png"])
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"nope")
    res, doc = run_cmd("sheet", ok, bad, "--out", tmp_path / "s.png", "--background", "#ffffff", "--cell", "64")
    assert res.exit_code == 0 and doc["warnings"] and Image.open(tmp_path / "s.png").convert("RGB").getpixel((1, 1)) == (255, 255, 255)
    assert run_cmd("sheet", ok, "--out", tmp_path / "s.png")[1]["items"][0]["status"] == "skipped_existing"
    for args in (["--columns", "0"], ["--cell", "5"], ["--background", "zzz"]):
        assert run_cmd("sheet", ok, "--out", tmp_path / "e.png", *args)[0].exit_code == 1
    assert run_cmd("sheet", ok, "--out", tmp_path / "e.txt")[0].exit_code == 1


# ------------------------------------------------------------------------------ profile
def test_to_cmyk_tiff_uses_the_engine_and_embeds_the_profile(tmp_path):
    src = photo(tmp_path / "p.png")
    res, doc = run_cmd("profile", src, "--to", "cmyk", "--cmyk-mode", "clean")
    out = Image.open(doc["items"][0]["output"])
    assert out.mode == "CMYK" and out.info.get("icc_profile") and out.size == (400, 300)
    assert all(round(v / 255 * 100) % 5 == 0 for v in out.getpixel((300, 250)))               # snapped to multiples of 5
    assert any("clean" in n for n in doc["items"][0]["notes"])


def test_to_cmyk_flattens_transparency_with_a_note(tmp_path):
    rgba = Image.new("RGBA", (20, 20), (0, 0, 0, 0))
    src = tmp_path / "t.png"
    rgba.save(src)
    res, doc = run_cmd("profile", src, "--to", "cmyk")
    assert Image.open(doc["items"][0]["output"]).getpixel((5, 5)) == (0, 0, 0, 0) and any("transparency" in n for n in doc["items"][0]["notes"])


@pytest.mark.skipif(not P3.exists(), reason="no Display P3 profile on this system")
def test_to_srgb_converts_a_wide_gamut_image(tmp_path):
    p3 = P3.read_bytes()
    src = tmp_path / "p3.png"
    Image.new("RGB", (20, 20), (230, 120, 40)).save(src, icc_profile=p3)
    res, doc = run_cmd("profile", src, "--to", "srgb")
    out = Image.open(doc["items"][0]["output"]).convert("RGB")
    r, g, b = out.getpixel((5, 5))
    assert abs(r - 230) + abs(g - 120) + abs(b - 40) > 15           # the numbers change: they were P3 numbers
    plain = tmp_path / "plain.png"
    Image.new("RGB", (20, 20), (255, 0, 0)).save(plain)
    res, doc = run_cmd("profile", plain, "--to", "srgb")
    assert any("assumed" in n for n in doc["items"][0]["notes"]) and Image.open(doc["items"][0]["output"]).convert("RGB").getpixel((5, 5)) == (255, 0, 0)


def test_profile_errors_and_overwrite(tmp_path):
    src = photo(tmp_path / "p.png")
    assert run_cmd("profile", src, "--to", "lab")[0].exit_code == 1
    assert run_cmd("profile", src, "--to", "cmyk", "--format", "png")[0].exit_code == 1
    assert run_cmd("profile", src, "--to", "cmyk", "--cmyk-mode", "rich")[0].exit_code == 1
    run_cmd("profile", src, "--to", "cmyk")
    assert run_cmd("profile", src, "--to", "cmyk")[1]["items"][0]["status"] == "skipped_existing"
    svg = tmp_path / "l.svg"
    svg.write_text(SIMPLE_SVG)
    assert run_cmd("profile", svg, "--to", "srgb")[0].exit_code == 1
