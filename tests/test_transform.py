from __future__ import annotations

import json
import shutil
import subprocess

import pytest
from PIL import Image

from mysuite.cli import app
from mysuite.transform import transform as T
from tests.helpers import SIMPLE_SVG, runner


def make(path, size=(100, 50), color=(255, 0, 0), mode="RGB"):
    Image.new(mode, size, color).save(path)
    return path


def run_t(*args):
    res = runner.invoke(app, ["transform", *[str(a) for a in args], "--json"])
    return res, json.loads(res.stdout)


def out_of(doc, i=0):
    return Image.open(doc["items"][i]["output"])


# ------------------------------------------------------------------- parsers
@pytest.mark.parametrize("fn,good,bad", [
    (T.parse_crop, ["10x20+1+2"], ["10x20", "0x5+0+0", "ax1+0+0", "10x20+-1+0"]),
    (T.parse_aspect, ["16:9", "1:1", "1.91:1"], ["16/9", "0:1", "1:0", "x"]),
    (T.parse_resize, ["512", "x512", "512x256", "50%", "512X"], ["", "x", "0x0", "-5", "abc", "0%"]),
    (T.parse_pad, ["1:1", "1200x630"], ["1200", "0x5", "square"]),
    (T.parse_radius, ["24", "50%", "12.5"], ["0", "-3", "60%", "big"]),
])
def test_parsers_accept_good_and_reject_bad(fn, good, bad):
    for g in good:
        fn(g)
    for b in bad:
        with pytest.raises(T.TransformError):
            fn(b)


@pytest.mark.parametrize("kw,fragment", [
    ({"crop": "10x10+0+0", "crop_aspect": "1:1"}, "either"),
    ({"background": "notacolour"}, "background"),
    ({"pad_color": "zzz"}, "pad_color"),
    ({"gravity": "up"}, "gravity"),
    ({"flip": "diagonal"}, "flip"),
    ({"resize_mode": "stretchy"}, "resize_mode"),
    ({"format": "gif"}, "format"),
    ({"quality": 0}, "quality"),
])
def test_ops_validate(kw, fragment):
    with pytest.raises(T.TransformError, match=fragment):
        T.Ops(**kw).validate()


# ------------------------------------------------------------------ operations on pixels
def test_resize_modes(tmp_path):
    p = make(tmp_path / "a.png", (200, 100))
    for spec, mode, expect in [("100", "fit", (100, 50)), ("x40", "fit", (80, 40)), ("100x100", "fit", (100, 50)),
                               ("100x100", "fill", (100, 100)), ("100x100", "exact", (100, 100)), ("50%", "fit", (100, 50)),
                               ("400", "fit", (400, 200))]:
        res, doc = run_t(p, "--resize", spec, "--resize-mode", mode, "--overwrite")
        assert out_of(doc).size == expect, (spec, mode)


def test_shrink_only_never_enlarges(tmp_path):
    p = make(tmp_path / "a.png", (50, 50))
    res, doc = run_t(p, "--resize", "200", "--shrink-only")
    assert out_of(doc).size == (50, 50)


def test_fill_crops_the_right_part(tmp_path):
    im = Image.new("RGB", (200, 100), (0, 0, 255))
    im.paste((255, 0, 0), (0, 0, 100, 100))          # left half red
    p = tmp_path / "a.png"
    im.save(p)
    _, doc = run_t(p, "--resize", "100x100", "--resize-mode", "fill", "--gravity", "left")
    assert out_of(doc).getpixel((50, 50)) == (255, 0, 0)
    _, doc = run_t(p, "--resize", "100x100", "--resize-mode", "fill", "--gravity", "right", "--overwrite")
    assert out_of(doc).getpixel((50, 50)) == (0, 0, 255)


def test_crop_box_and_out_of_bounds(tmp_path):
    p = make(tmp_path / "a.png", (100, 100))
    res, doc = run_t(p, "--crop", "40x30+10+20")
    assert out_of(doc).size == (40, 30)
    res, doc = run_t(p, "--crop", "200x30+0+0", "--overwrite")
    assert res.exit_code == 1 and doc["items"][0]["status"] == "failed" and "outside" in doc["items"][0]["error"]


def test_crop_aspect_with_gravity(tmp_path):
    im = Image.new("RGB", (300, 100), (0, 0, 255))
    im.paste((255, 0, 0), (0, 0, 100, 100))
    p = tmp_path / "a.png"
    im.save(p)
    _, doc = run_t(p, "--crop-aspect", "1:1", "--gravity", "left")
    out = out_of(doc)
    assert out.size == (100, 100) and out.getpixel((50, 50)) == (255, 0, 0)
    _, doc = run_t(p, "--crop-aspect", "3:1", "--overwrite")
    assert out_of(doc).size == (300, 100)           # already that aspect: untouched


def test_trim_removes_uniform_and_transparent_borders(tmp_path):
    im = Image.new("RGB", (100, 100), "white")
    im.paste((255, 0, 0), (30, 40, 70, 60))
    p = tmp_path / "w.png"
    im.save(p)
    _, doc = run_t(p, "--trim")
    assert out_of(doc).size == (40, 20)
    rgba = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    rgba.paste((0, 255, 0, 255), (10, 10, 30, 50))
    q = tmp_path / "t.png"
    rgba.save(q)
    _, doc = run_t(q, "--trim")
    assert out_of(doc).size == (20, 40)


def test_trim_fuzz_and_all_border_image(tmp_path):
    im = Image.new("RGB", (60, 60), (250, 250, 250))
    im.paste((255, 255, 255), (20, 20, 40, 40))        # nearly the same as the border
    p = tmp_path / "f.png"
    im.save(p)
    assert out_of(run_t(p, "--trim")[1]).size == (20, 20)
    assert out_of(run_t(p, "--trim", "--trim-fuzz", "5", "--overwrite")[1]).size == (60, 60)   # all within fuzz: left alone
    blank = make(tmp_path / "b.png", (30, 30), (9, 9, 9))
    assert out_of(run_t(blank, "--trim")[1]).size == (30, 30)                                     # never an empty image


def test_rotate_and_flip(tmp_path):
    im = Image.new("RGB", (100, 50), (0, 0, 255))
    im.paste((255, 0, 0), (0, 0, 10, 10))              # red top-left
    p = tmp_path / "a.png"
    im.save(p)
    out = out_of(run_t(p, "--rotate", "90")[1])
    assert out.size == (50, 100) and out.getpixel((45, 5)) == (255, 0, 0)      # clockwise: top-left -> top-right
    out = out_of(run_t(p, "--flip", "horizontal", "--overwrite")[1])
    assert out.getpixel((95, 5)) == (255, 0, 0)
    out = out_of(run_t(p, "--flip", "both", "--overwrite")[1])
    assert out.getpixel((95, 45)) == (255, 0, 0)
    out = out_of(run_t(p, "--rotate", "45", "--overwrite")[1])
    assert out.size[0] > 100 and out.mode == "RGBA" and out.getpixel((0, 0))[3] == 0


def test_pad_to_aspect_and_size(tmp_path):
    p = make(tmp_path / "a.png", (200, 100))
    out = out_of(run_t(p, "--pad", "1:1", "--pad-color", "#00ff00")[1])
    assert out.size == (200, 200) and out.getpixel((5, 5))[:3] == (0, 255, 0) and out.getpixel((100, 100))[:3] == (255, 0, 0)
    out = out_of(run_t(p, "--pad", "400x300", "--gravity", "top-left", "--overwrite")[1])
    assert out.size == (400, 300) and out.getpixel((10, 10))[:3] == (255, 0, 0) and out.getpixel((300, 250))[3] == 0
    res, doc = run_t(p, "--pad", "100x100", "--overwrite")
    assert res.exit_code == 1 and "smaller" in doc["items"][0]["error"]


def test_round_corners_and_circle(tmp_path):
    p = make(tmp_path / "a.png", (100, 100))
    out = out_of(run_t(p, "--round", "30")[1])
    assert out.mode == "RGBA" and out.getpixel((0, 0))[3] == 0 and out.getpixel((50, 50))[3] == 255
    out = out_of(run_t(p, "--round", "50%", "--overwrite")[1])
    assert out.getpixel((2, 2))[3] == 0 and out.getpixel((50, 2))[3] > 200


def test_background_flatten_and_jpeg_default_white(tmp_path):
    rgba = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
    rgba.paste((255, 0, 0, 255), (20, 0, 40, 40))
    p = tmp_path / "t.png"
    rgba.save(p)
    out = out_of(run_t(p, "--background", "#0000ff")[1])
    assert out.mode == "RGB" and out.getpixel((2, 2)) == (0, 0, 255)
    res, doc = run_t(p, "--format", "jpeg")
    assert Image.open(doc["items"][0]["output"]).getpixel((2, 2))[0] > 240 and any("flattened" in n for n in doc["items"][0]["notes"])


def test_fixed_operation_order(tmp_path):
    """Whatever the flag order: trim -> crop -> rotate -> resize -> pad."""
    im = Image.new("RGB", (120, 80), "white")
    im.paste((255, 0, 0), (20, 10, 100, 70))          # 80x60 content
    p = tmp_path / "a.png"
    im.save(p)
    _, doc = run_t(p, "--pad", "1:1", "--resize", "40", "--trim")
    assert out_of(doc).size == (40, 40)                # trim 80x60 -> resize 40x30 -> pad square 40x40
    assert doc["items"][0]["applied"] == ["trim", "resize", "pad"]


def test_exif_orientation_is_applied_and_metadata_dropped(tmp_path):
    if not shutil.which("exiftool") or not shutil.which("magick"):
        pytest.skip("needs exiftool")
    p = tmp_path / "r.jpg"
    subprocess.run(["magick", "-size", "200x100", "xc:#808080", str(p)], check=True)
    subprocess.run(["exiftool", "-q", "-overwrite_original", "-Orientation#=6", "-GPSLatitude=52.5", "-GPSLatitudeRef=N",
                    "-SerialNumber=SN1", str(p)], check=True)
    _, doc = run_t(p, "--resize", "50%")
    out = doc["items"][0]["output"]
    assert Image.open(out).size == (50, 100)                                     # rotated upright, then halved
    tags = subprocess.run(["exiftool", "-GPS:all", "-SerialNumber", "-Orientation", out], capture_output=True, text=True).stdout
    assert tags.strip() == ""


def test_format_conversion_and_quality(tmp_path):
    p = make(tmp_path / "a.png", (64, 64))
    for fmt, ext in (("jpeg", ".jpg"), ("webp", ".webp"), ("tiff", ".tiff")):
        _, doc = run_t(p, "--format", fmt, "--quality", "50")
        assert doc["items"][0]["output"].endswith(f"a_transformed{ext}")
    assert Image.open(tmp_path / "a_transformed.webp").format == "WEBP"


def test_cmyk_animated_and_16bit_notes(tmp_path):
    if not shutil.which("magick"):
        pytest.skip("needs magick")
    cmyk = tmp_path / "c.tiff"
    subprocess.run(["magick", "-size", "20x20", "xc:#dd0000", "-colorspace", "CMYK", str(cmyk)], check=True)
    gif = tmp_path / "g.gif"
    subprocess.run(["magick", "-delay", "5", "-size", "20x20", "xc:red", "xc:blue", str(gif)], check=True)
    deep = tmp_path / "d.png"
    subprocess.run(["magick", "-size", "20x20", "gradient:black-white", "-depth", "16", str(deep)], check=True)
    _, doc = run_t(cmyk, gif, deep, "--resize", "10", "--format", "png")
    notes = {i["input"].rsplit("/", 1)[1]: " ".join(i["notes"]) for i in doc["items"]}
    assert "CMYK" in notes["c.tiff"] and "frames" in notes["g.gif"] and "8 bits" in notes["d.png"]


@pytest.mark.skipif(not shutil.which("rsvg-convert"), reason="needs rsvg-convert")
def test_svg_source_becomes_png(tmp_path):
    svg = tmp_path / "logo.svg"
    svg.write_text(SIMPLE_SVG)
    _, doc = run_t(svg, "--resize", "100", "--round", "20%")
    out = out_of(doc)
    assert out.format == "PNG" and out.width == 100 and out.getpixel((0, 0))[3] == 0


# ------------------------------------------------------------------- behaviour
def test_nothing_to_do_and_bad_values_are_clean_errors(tmp_path):
    p = make(tmp_path / "a.png")
    res = runner.invoke(app, ["transform", str(p), "--json"])
    assert res.exit_code == 1 and "nothing to do" in json.loads(res.stdout)["errors"][0]
    res = runner.invoke(app, ["transform", str(p), "--resize", "abc", "--json"])
    assert res.exit_code == 1 and "resize must be" in json.loads(res.stdout)["errors"][0]
    assert not list(tmp_path.glob("*_transformed*"))


def test_never_overwrites_and_original_untouched(tmp_path):
    p = make(tmp_path / "a.png")
    before = p.read_bytes()
    _, doc = run_t(p, "--rotate", "90")
    out = tmp_path / "a_transformed.png"
    first = out.read_bytes()
    _, doc = run_t(p, "--rotate", "180")
    assert doc["items"][0]["status"] == "skipped_existing" and out.read_bytes() == first
    _, doc = run_t(p, "--rotate", "180", "--overwrite")
    assert doc["items"][0]["status"] == "written" and out.read_bytes() != first
    assert p.read_bytes() == before


def test_dry_run_and_batch_and_failure(tmp_path):
    a, b = make(tmp_path / "a.png"), make(tmp_path / "b.png")
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"nope")
    res, doc = run_t(a, "--rotate", "90", "--dry-run")
    assert doc["items"][0]["status"] == "planned" and not (tmp_path / "a_transformed.png").exists()
    res, doc = run_t(a, b, bad, "--flip", "both")
    assert res.exit_code == 1 and sorted(i["status"] for i in doc["items"]) == ["failed", "written", "written"]


def test_suffix_that_would_replace_the_input_is_refused(tmp_path):
    p = make(tmp_path / "a.png")
    res, doc = run_t(p, "--rotate", "90", "--suffix", "")
    assert res.exit_code == 1 and "replace the input" in doc["items"][0]["error"]
    assert Image.open(p).size == (100, 50)


def test_huge_image_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "MAX_PIXELS", 100)
    p = make(tmp_path / "a.png", (50, 50))
    res, doc = run_t(p, "--rotate", "90")
    assert res.exit_code == 1 and "limit" in doc["items"][0]["error"]


def test_sandbox_applies(tmp_path):
    jail = tmp_path / "jail"
    jail.mkdir()
    p = make(tmp_path / "out.png")
    res = runner.invoke(app, ["--allow", str(jail), "transform", str(p), "--rotate", "90", "--json"])
    assert res.exit_code == 3 and not (tmp_path / "out_transformed.png").exists()
