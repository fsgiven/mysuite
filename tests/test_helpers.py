from __future__ import annotations

import json
import shutil

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from mysuite.cli import app
from mysuite.helpers import core
from tests.helpers import runner

need_vision = pytest.mark.skipif(shutil.which("mysuite-vision") is None, reason="needs the macOS mysuite-vision helper")


def run_cmd(*args):
    res = runner.invoke(app, [str(a) for a in args] + ["--json"])
    return res, json.loads(res.stdout)


def noise_image(path, seed=1, size=(120, 90)):
    rng = np.random.default_rng(seed)
    base = rng.integers(0, 255, (size[1] // 10, size[0] // 10, 3), dtype=np.uint8)
    Image.fromarray(base).resize(size, Image.Resampling.BICUBIC).save(path)
    return path


# ---------------------------------------------------------------------------- contrast
@pytest.mark.parametrize("fg,bg,ratio", [("#000", "#fff", 21.0), ("#fff", "#fff", 1.0), ("#777777", "#ffffff", 4.48), ("#767676", "#ffffff", 4.54)])
def test_known_wcag_ratios(fg, bg, ratio):
    assert core.check_colours(fg, bg)["ratio"] == pytest.approx(ratio, abs=0.01)


def test_grades_follow_the_thresholds():
    assert core.check_colours("#777777", "#ffffff")["AA_normal_text"] is False       # 4.48 < 4.5
    assert core.check_colours("#767676", "#ffffff")["AA_normal_text"] is True
    r = core.check_colours("#000", "#fff")
    assert all(r[k] for k in ("AA_normal_text", "AAA_normal_text", "graphics_and_ui_AA"))
    assert core.check_colours("#ffff00", "#ffffff")["graphics_and_ui_AA"] is False


@pytest.mark.parametrize("fg,bg", [("nope", "#fff"), ("#fff", "zzz"), ("rgba(0,0,0,.5)", "#fff")])
def test_contrast_rejects_bad_colours(fg, bg):
    with pytest.raises(core.HelperError):
        core.check_colours(fg, bg)


def test_contrast_cli_and_logo_palette(tmp_path):
    res, doc = run_cmd("contrast", "#dd0000", "white")
    assert doc["items"][0]["ratio"] == pytest.approx(5.15, abs=0.01) and doc["items"][0]["AA_normal_text"]
    svg = tmp_path / "l.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg"><rect fill="#ffee00" width="9" height="9"/><rect fill="#000" width="9" height="9"/></svg>')
    res, doc = run_cmd("contrast", "--logo", svg, "--on", "#ffffff")
    by = {i["foreground"]: i for i in doc["items"]}
    assert by["#ffee00"]["graphics_and_ui_AA"] is False and by["#000000"]["ratio"] == 21.0
    assert run_cmd("contrast")[0].exit_code == 1
    assert run_cmd("contrast", "red", "nope")[0].exit_code == 1


# ------------------------------------------------------------------------------- dupes
def test_dupes_finds_identical_resized_and_recompressed(tmp_path):
    a = noise_image(tmp_path / "a.png", 1, (160, 120))
    (tmp_path / "copy.png").write_bytes(a.read_bytes())
    Image.open(a).resize((80, 60), Image.Resampling.LANCZOS).save(tmp_path / "small.png")
    Image.open(a).convert("RGB").save(tmp_path / "re.jpg", quality=60)
    noise_image(tmp_path / "other.png", 99, (160, 120))
    res, doc = run_cmd("dupes", tmp_path)
    groups = [i for i in doc["items"] if i["status"] == "group"]
    assert len(groups) == 1
    names = {f["path"].rsplit("/", 1)[1] for f in groups[0]["files"]}
    assert names == {"a.png", "copy.png", "small.png", "re.jpg"} and groups[0]["kind"] == "similar"
    assert groups[0]["keep_suggestion"].endswith(("a.png", "copy.png"))               # the biggest picture is the keeper
    assert doc["scanned"] == 5 and doc["wasted_bytes"] > 0


def test_identical_only_group_is_labelled_identical_and_is_read_only(tmp_path):
    a = noise_image(tmp_path / "a.png")
    (tmp_path / "b.png").write_bytes(a.read_bytes())
    before = sorted((p.name, p.read_bytes()) for p in tmp_path.iterdir())
    res, doc = run_cmd("dupes", tmp_path)
    assert [g["kind"] for g in doc["items"]] == ["identical"]
    assert sorted((p.name, p.read_bytes()) for p in tmp_path.iterdir()) == before


def test_dupes_threshold_zero_and_none_found(tmp_path):
    noise_image(tmp_path / "a.png", 1)
    noise_image(tmp_path / "b.png", 2)
    res, doc = run_cmd("dupes", tmp_path)
    assert doc["groups"] == 0 and res.exit_code == 0
    assert run_cmd("dupes", tmp_path, "--threshold", "99")[0].exit_code == 1


def test_dupes_reports_unreadable_files_without_stopping(tmp_path):
    noise_image(tmp_path / "a.png")
    (tmp_path / "bad.png").write_bytes(b"nope")
    res, doc = run_cmd("dupes", tmp_path)
    assert res.exit_code == 1 and any(i["status"] == "failed" for i in doc["items"]) and doc["scanned"] == 2


def test_dupes_recursive(tmp_path):
    a = noise_image(tmp_path / "a.png")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "a_copy.png").write_bytes(a.read_bytes())
    assert run_cmd("dupes", tmp_path)[1]["groups"] == 0
    assert run_cmd("dupes", tmp_path, "--recursive")[1]["groups"] == 1


# --------------------------------------------------------------------------------- diff
def test_diff_identical_and_changed(tmp_path):
    a = noise_image(tmp_path / "a.png")
    (tmp_path / "same.png").write_bytes(a.read_bytes())
    res, doc = run_cmd("diff", a, tmp_path / "same.png")
    item = doc["items"][0]
    assert item["identical"] and item["changed_pct"] == 0 and item["psnr_db"] is None
    im = Image.open(a).convert("RGB")
    ImageDraw.Draw(im).rectangle([10, 10, 29, 29], fill=(255, 255, 255))                     # 20x20 of 120x90
    im.save(tmp_path / "edit.png")
    res, doc = run_cmd("diff", a, tmp_path / "edit.png", "--out", tmp_path / "d.png")
    item = doc["items"][0]
    assert not item["identical"] and 2.5 < item["changed_pct"] < 4.0 and item["psnr_db"] > 0
    out = Image.open(tmp_path / "d.png").convert("RGB")
    assert out.size == (120, 90) and out.getpixel((20, 20)) == (255, 0, 60) and out.getpixel((100, 80)) != (255, 0, 60)


def test_diff_size_mismatch_is_a_warning_and_threshold_matters(tmp_path):
    a = noise_image(tmp_path / "a.png", 1, (120, 90))
    Image.open(a).resize((60, 45)).save(tmp_path / "b.png")
    res, doc = run_cmd("diff", a, tmp_path / "b.png")
    assert doc["items"][0]["same_size"] is False and doc["warnings"]
    base = Image.new("RGB", (20, 20), (100, 100, 100))
    base.save(tmp_path / "x.png")
    Image.new("RGB", (20, 20), (110, 100, 100)).save(tmp_path / "y.png")
    assert run_cmd("diff", tmp_path / "x.png", tmp_path / "y.png", "--threshold", "16")[1]["items"][0]["changed_pct"] == 0
    assert run_cmd("diff", tmp_path / "x.png", tmp_path / "y.png", "--threshold", "5")[1]["items"][0]["changed_pct"] == 100


def test_diff_bad_input_and_no_overwrite(tmp_path):
    a = noise_image(tmp_path / "a.png")
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"x")
    assert run_cmd("diff", a, bad)[0].exit_code == 1
    Image.open(a).save(tmp_path / "b.png")
    run_cmd("diff", a, tmp_path / "b.png", "--out", tmp_path / "d.png")
    first = (tmp_path / "d.png").read_bytes()
    res, doc = run_cmd("diff", a, tmp_path / "b.png", "--out", tmp_path / "d.png")
    assert doc["items"][0]["diff_image"]["status"] == "skipped_existing" and (tmp_path / "d.png").read_bytes() == first


# ----------------------------------------------------------------------------------- qr make
def test_qr_make_png_and_svg(tmp_path):
    res, doc = run_cmd("qr", "make", "https://example.com", "--out", tmp_path / "q.png", "--scale", "5", "--border", "2")
    item = doc["items"][0]
    assert item["status"] == "written"
    with Image.open(tmp_path / "q.png") as im:
        assert im.size[0] == im.size[1] == (item["modules"] + 4) * 5
    res, doc = run_cmd("qr", "make", "hello", "--out", tmp_path / "q.svg", "--dark", "#336699")
    svg = (tmp_path / "q.svg").read_text().lower()
    assert "#336699" in svg or "#369" in svg                                     # segno shortens #336699
    res, doc = run_cmd("qr", "make", "hello", "--out", tmp_path / "q.svg")
    assert doc["items"][0]["status"] == "skipped_existing"


@pytest.mark.parametrize("args", [["x" * 3000], ["a", "--error", "z"], ["a", "--scale", "0"], ["a", "--dark", "nope"], [""]])
def test_qr_make_validation(tmp_path, args):
    res, doc = run_cmd("qr", "make", *args, "--out", tmp_path / "q.png")
    assert res.exit_code == 1 and not (tmp_path / "q.png").exists()


@need_vision
def test_qr_round_trip_through_vision(tmp_path):
    run_cmd("qr", "make", "https://example.com/round-trip?x=1", "--out", tmp_path / "q.png")
    res, doc = run_cmd("qr", "read", tmp_path / "q.png")
    assert doc["items"][0]["codes"][0]["payload"] == "https://example.com/round-trip?x=1"
    blank = Image.new("RGB", (100, 100), "white")
    blank.save(tmp_path / "blank.png")
    assert run_cmd("qr", "read", tmp_path / "blank.png")[1]["items"][0]["count"] == 0


# ------------------------------------------------------------------------------------- ocr
@need_vision
def test_ocr_reads_text_and_saves_beside_the_image(tmp_path):
    im = Image.new("RGB", (700, 220), "white")
    d = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 56)
    except OSError:
        pytest.skip("no font")
    d.text((20, 20), "Invoice 4711", fill="black", font=font)
    d.text((20, 110), "Total 99 EUR", fill="black", font=font)
    im.save(tmp_path / "t.png")
    res, doc = run_cmd("ocr", tmp_path / "t.png", "--save")
    item = doc["items"][0]
    assert "Invoice 4711" in item["text"] and "Total 99 EUR" in item["text"] and item["line_count"] == 2
    assert item["lines"][0]["box"]["y"] < item["lines"][1]["box"]["y"]                       # reading order, top first
    assert "Invoice 4711" in (tmp_path / "t.txt").read_text()
    res, doc = run_cmd("ocr", tmp_path / "t.png", "--save")
    assert doc["items"][0]["saved"]["status"] == "skipped_existing"
    assert run_cmd("ocr", tmp_path / "t.png", "--languages", "not a language")[0].exit_code == 1


def test_vision_tools_report_a_missing_helper_with_exit_4(tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text('[tools]\nvision_tool = "mysuite-vision-NOT-INSTALLED"\n')
    img = noise_image(tmp_path / "a.png")
    import os

    os.environ["MYSUITE_CONFIG"] = str(cfg)
    try:
        from mysuite.doctor import require_tools
        from mysuite.config import ToolPaths
        import typer

        with pytest.raises(typer.Exit) as exc:
            require_tools(ToolPaths(vision_tool="mysuite-vision-NOT-INSTALLED"), "ocr")
        assert exc.value.exit_code == 4
    finally:
        os.environ.pop("MYSUITE_CONFIG", None)


def test_sandbox_applies_to_helpers(tmp_path):
    jail = tmp_path / "jail"
    jail.mkdir()
    a = noise_image(tmp_path / "a.png")
    assert runner.invoke(app, ["--allow", str(jail), "dupes", str(a), "--json"]).exit_code == 3
    assert runner.invoke(app, ["--allow", str(jail), "diff", str(a), str(a), "--json"]).exit_code == 3
    res = runner.invoke(app, ["--allow", str(jail), "qr", "make", "x", "--out", str(tmp_path / "q.png"), "--json"])
    assert res.exit_code == 3 and not (tmp_path / "q.png").exists()
