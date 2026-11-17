"""WS-E helper fixes: qr output formats and contrast, dupes false positives and readable names, diff of
SVG/PDF, contrast decimals and --tokens, and a one-line missing-tool message."""
from __future__ import annotations

import json
import shutil

import numpy as np
import pytest
from PIL import Image

from mysuite.cli import app
from mysuite.helpers import core
from tests.helpers import SIMPLE_SVG, runner

need_rsvg = pytest.mark.skipif(shutil.which("rsvg-convert") is None, reason="needs rsvg-convert")
need_gs = pytest.mark.skipif(shutil.which("gs") is None, reason="needs ghostscript")


def run_cmd(*args):
    res = runner.invoke(app, [str(a) for a in args] + ["--json"])
    return res, json.loads(res.stdout)


# ------------------------------------------------------------------------------------------- qr
def test_qr_make_jpg_writes_a_real_jpeg(tmp_path):
    res, doc = run_cmd("qr", "make", "hello", "--out", tmp_path / "q.jpg")
    assert res.exit_code == 0, doc
    assert doc["items"][0]["output"] == str(tmp_path / "q.jpg")
    with Image.open(tmp_path / "q.jpg") as im:
        assert im.format == "JPEG"
    assert not (tmp_path / "q.png").exists()


def test_qr_make_refuses_an_unknown_extension(tmp_path):
    res, doc = run_cmd("qr", "make", "hello", "--out", tmp_path / "q.gif")
    assert res.exit_code == 1 and ".gif" in doc["errors"][0] and ".png" in doc["errors"][0]
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("dark,light", [("#bbbbbb", "#ffffff"), ("#ffffff", "#000000")])
def test_qr_make_warns_about_hard_to_scan_colours(tmp_path, dark, light):
    res, doc = run_cmd("qr", "make", "hello", "--out", tmp_path / "q.png", "--dark", dark, "--light", light)
    assert res.exit_code == 0 and (tmp_path / "q.png").exists()
    assert doc["warnings"] and ("contrast" in doc["warnings"][0] or "lighter" in doc["warnings"][0])


def test_qr_make_default_colours_have_no_warning(tmp_path):
    res, doc = run_cmd("qr", "make", "hello", "--out", tmp_path / "q.png")
    assert doc["warnings"] == []


# ---------------------------------------------------------------------------------------- dupes
def _gradient(path, size=(160, 120), mode="RGB", horizontal=True):
    w, h = size
    ramp = np.linspace(0, 255, w if horizontal else h)
    a = np.tile(ramp, (h, 1)) if horizontal else np.tile(ramp[:, None], (1, w))
    Image.fromarray(np.dstack([a] * 3).astype(np.uint8)).save(path)
    return path


def test_flat_and_gradient_pictures_are_not_look_alikes(tmp_path):
    Image.new("RGB", (160, 120), "#3366cc").save(tmp_path / "flat.png")
    frames = [Image.new("RGB", (160, 120), c) for c in ("#000000", "#ff0000", "#00ff00")]
    frames[0].save(tmp_path / "anim.gif", save_all=True, append_images=frames[1:])
    _gradient(tmp_path / "ramp.png", (450, 300))
    grey16 = (np.random.default_rng(3).integers(0, 65535, (40, 60))).astype(np.uint16)
    Image.fromarray(grey16).resize((600, 400)).save(tmp_path / "grey16.png")
    res, doc = run_cmd("dupes", tmp_path)
    groups = [i for i in doc["items"] if i["status"] == "group"]
    assert groups == [], groups


def test_16bit_png_is_hashed_from_its_real_content(tmp_path):
    a = (np.random.default_rng(1).integers(0, 65535, (30, 40))).astype(np.uint16)
    Image.fromarray(a).resize((400, 300)).save(tmp_path / "big.png")
    Image.fromarray(a).resize((200, 150)).save(tmp_path / "small.png")
    res, doc = run_cmd("dupes", tmp_path)
    groups = [i for i in doc["items"] if i["status"] == "group"]
    assert len(groups) == 1 and groups[0]["kind"] == "similar"


def test_different_aspect_ratios_are_not_look_alikes(tmp_path):
    rng = np.random.default_rng(5)
    base = Image.fromarray(rng.integers(0, 255, (12, 16, 3), dtype=np.uint8)).resize((160, 120), Image.Resampling.BICUBIC)
    base.save(tmp_path / "a.png")
    base.resize((320, 120)).save(tmp_path / "stretched.png")
    res, doc = run_cmd("dupes", tmp_path)
    assert not [i for i in doc["items"] if i["status"] == "group"]


def test_dupes_human_output_shows_the_name_first(tmp_path):
    deep = tmp_path / ("a-very-long-folder-name-" * 4) / "photos"
    deep.mkdir(parents=True)
    Image.new("RGB", (30, 20), "red").save(deep / "holiday.png")
    (deep / "holiday copy.png").write_bytes((deep / "holiday.png").read_bytes())
    res = runner.invoke(app, ["dupes", str(tmp_path), "--recursive"], env={"COLUMNS": "80"})
    assert "holiday.png" in res.output and "holiday copy.png" in res.output
    res, doc = run_cmd("dupes", tmp_path, "--recursive")
    files = doc["items"][0]["files"]
    assert {f["name"] for f in files} == {"holiday.png", "holiday copy.png"}


# ----------------------------------------------------------------------------------------- diff
@need_rsvg
def test_diff_accepts_svg(tmp_path):
    (tmp_path / "a.svg").write_text(SIMPLE_SVG)
    (tmp_path / "b.svg").write_text(SIMPLE_SVG.replace("#dd0000", "#0057b8"))
    res, doc = run_cmd("diff", tmp_path / "a.svg", tmp_path / "a.svg")
    assert res.exit_code == 0 and doc["items"][0]["identical"] is True
    res, doc = run_cmd("diff", tmp_path / "a.svg", tmp_path / "b.svg", "--out", tmp_path / "d.png")
    assert res.exit_code == 0 and doc["items"][0]["identical"] is False and (tmp_path / "d.png").exists()
    assert any("rendered" in w for w in doc["warnings"])


@need_gs
def test_diff_accepts_pdf_page_one(tmp_path):
    Image.new("RGB", (100, 140), "white").save(tmp_path / "a.pdf", format="PDF")
    Image.new("RGB", (100, 140), "black").save(tmp_path / "b.pdf", format="PDF")
    res, doc = run_cmd("diff", tmp_path / "a.pdf", tmp_path / "b.pdf")
    assert res.exit_code == 0, doc
    assert doc["items"][0]["changed_pct"] > 90


# ------------------------------------------------------------------------------------- contrast
def test_contrast_prints_two_decimals_everywhere():
    res = runner.invoke(app, ["contrast", "#000000", "#ffffff"])
    assert "21.00:1" in res.output
    res = runner.invoke(app, ["contrast", "#777777", "#ffffff"])
    assert "4.48:1" in res.output


def test_contrast_takes_token_names(tmp_path):
    (tmp_path / "tokens.css").write_text(':root{--brand-red:#dd0000;--paper:#ffffff}[data-theme="dark"]{--brand-red:#ff6b6b;--paper:#111111}')
    res, doc = run_cmd("contrast", "token:--brand-red", "token:paper", "--tokens", tmp_path / "tokens.css")
    assert res.exit_code == 0, doc
    item = doc["items"][0]
    assert item["foreground"] == "#dd0000" and item["background"] == "#ffffff"
    assert item["foreground_token"] == "--brand-red"
    res, dark = run_cmd("contrast", "token:--brand-red", "token:--paper", "--tokens", tmp_path / "tokens.css", "--theme", "dark")
    assert dark["items"][0]["foreground"] == "#ff6b6b"
    res, doc = run_cmd("contrast", "token:--nope", "white", "--tokens", tmp_path / "tokens.css")
    assert res.exit_code == 1 and "--nope" in doc["errors"][0]
    res, doc = run_cmd("contrast", "token:--brand-red", "white")
    assert res.exit_code == 1 and "--tokens" in doc["errors"][0]


# ------------------------------------------------------------------------------- missing helper
def test_a_missing_tool_is_one_line_with_the_install_hint(capsys):
    import typer

    from mysuite.config import ToolPaths
    from mysuite.doctor import require_tools

    with pytest.raises(typer.Exit) as exc:
        require_tools(ToolPaths(vision_tool="mysuite-vision-NOT-INSTALLED"), "ocr")
    assert exc.value.exit_code == 4
    err = capsys.readouterr()
    text = err.out + err.err
    assert "mysuite doctor" not in text and "┏" not in text and "━" not in text   # no table
    lines = [l for l in text.splitlines() if l.strip()]
    assert len(lines) <= 2 and "mysuite install vision" in text
