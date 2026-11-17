"""Export naming: house-style templates must come out exactly as written (WS-A item 1).

Goal case: `RGB/PNG/20250514_BILD_Logo_positive_512_RGB.png` and, for vectors, `RGB/PDF/20250514_BILD_Logo_positive_RGB.pdf`.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from PIL import Image

from mysuite.export.naming import NamingError, fill, render_filename
from mysuite.export.units import Size
from tests.helpers import cli, write_svg
from tests.test_export_choices import plain

need = pytest.mark.skipif(not all(shutil.which(t) for t in ("rsvg-convert", "magick", "gs")), reason="needs rsvg/magick/gs")

HOUSE = "20250514_BILD_Logo_{variant}_{size}_{COLORSPACE}{ext}"


def _files(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


# ------------------------------------------------------------------ the template language
def test_uppercase_token_gives_uppercase_value():
    assert fill("{FORMAT}/{COLORSPACE}/{name}", {"format": "png", "colorspace": "rgb", "name": "Logo"}) == "PNG/RGB/Logo"


def test_empty_token_drops_its_separator():
    values = {"name": "logo", "variant": "", "size": "", "ext": ".pdf", "colorspace": "rgb"}
    assert fill("{name}_{variant}_{size}{ext}", values) == "logo.pdf"
    assert fill("{variant}_{name}{ext}", values) == "logo.pdf"
    assert fill(HOUSE, {**values, "variant": "positive"}) == "20250514_BILD_Logo_positive_RGB.pdf"


def test_empty_folder_segment_disappears():
    assert fill("{variant}/{format}/{naming}", {"variant": "", "format": "png", "naming": "a.png"}) == "png/a.png"


def test_unknown_token_is_a_friendly_error():
    with pytest.raises(NamingError, match=r"unknown token \{colour\}.*colorspace"):
        fill("{name}_{colour}", {"name": "x", "colorspace": "rgb"})


def test_broken_brace_is_a_friendly_error():
    with pytest.raises(NamingError, match="brace"):
        fill("{name", {"name": "x"})


def test_mixed_case_token_is_unknown():
    with pytest.raises(NamingError, match="unknown token"):
        fill("{Name}", {"name": "x"})


def test_width_and_height_are_the_real_pixels_not_the_size_label():
    name = render_filename("{name}_{width}x{height}{ext}", name="bug", size=Size("512", 512), format="png",
                           colorspace="rgb", width=512, height=300)
    assert name == "bug_512x300.png"


def test_width_without_known_dimensions_is_an_error():
    with pytest.raises(NamingError, match="width"):
        render_filename("{name}_{width}{ext}", name="bug", size=Size("512", 512), format="png", colorspace="rgb")


# ---------------------------------------------------------------------------- the CLI
def _wide(tmp_path: Path) -> Path:
    return write_svg(tmp_path / "bug.svg", '<rect width="333" height="127" fill="#dd0000"/>', width=333, height=127)


@need
def test_width_height_tokens_match_the_written_png(tmp_path):
    svg = _wide(tmp_path)
    res = cli("export", svg, "--formats", "png", "--sizes", "100,77", "--naming", "{name}_{width}x{height}{ext}",
              "--out", tmp_path / "o", "-q")
    assert res.exit_code == 0, res.stdout
    for png in (tmp_path / "o").rglob("*.png"):
        w, h = Image.open(png).size
        assert png.name == f"bug_{w}x{h}.png", png.name


@need
def test_width_height_include_the_margin(tmp_path):
    svg = _wide(tmp_path)
    res = cli("export", svg, "--formats", "png", "--sizes", "100", "--margin", "10", "--naming",
              "{name}_{width}x{height}{ext}", "--out", tmp_path / "o", "-q")
    assert res.exit_code == 0, res.stdout
    png = next((tmp_path / "o").rglob("*.png"))
    assert png.name == "bug_120x59.png" and Image.open(png).size == (120, 59)


def test_unknown_token_on_the_cli_exits_1_without_a_traceback(tmp_path):
    svg = _wide(tmp_path)
    res = cli("export", svg, "--formats", "png", "--sizes", "64", "--naming", "{name}_{colour}{ext}",
              "--out", tmp_path / "o", "--dry-run", "-q")
    assert res.exit_code == 1
    assert "Traceback" not in res.output and "unknown token {colour}" in plain(res.output)


def test_unknown_token_in_config_is_friendly_too(tmp_path):
    cfg = tmp_path / "mysuite.toml"
    cfg.write_text('[export]\npath_template = "{fmt}/{naming}"\n')
    res = cli("export", _wide(tmp_path), "--config", cfg, "--formats", "png", "--sizes", "64", "--out", tmp_path / "o",
              "--dry-run", "-q")
    assert res.exit_code == 1 and "unknown token {fmt}" in plain(res.output)


@need
def test_default_file_name_carries_the_variant(tmp_path):
    svg = _wide(tmp_path)
    res = cli("export", svg, "--formats", "png", "--sizes", "64", "--variants", "default,invert",
              "--out", tmp_path / "o", "-q")
    assert res.exit_code == 0, res.stdout
    assert _files(tmp_path / "o") == ["bug/invert/png/rgb/bug_invert_64.png", "bug/png/rgb/bug_64.png"]


@need
def test_house_naming_is_reproduced_exactly(tmp_path):
    svg = _wide(tmp_path)
    res = cli("export", svg, "--formats", "png", "--profiles", "rgb", "--sizes", "512,1080",
              "--variants", "positive=default", "--naming", HOUSE, "--path", "{COLORSPACE}/{FORMAT}/{naming}",
              "--no-name-folder", "--out", tmp_path / "o", "-q")
    assert res.exit_code == 0, res.stdout
    assert _files(tmp_path / "o") == [
        "RGB/PNG/20250514_BILD_Logo_positive_1080_RGB.png",
        "RGB/PNG/20250514_BILD_Logo_positive_512_RGB.png",
    ]


def test_no_name_folder_from_config(tmp_path):
    cfg = tmp_path / "mysuite.toml"
    cfg.write_text("[export]\nname_folder = false\n")
    res = cli("export", _wide(tmp_path), "--config", cfg, "--formats", "png", "--sizes", "64",
              "--out", tmp_path / "o", "--dry-run", "--json")
    assert res.exit_code == 0, res.stdout
    out = json.loads(res.stdout)["items"][0]["output"]
    assert out == str((tmp_path / "o" / "png" / "rgb" / "bug_64.png").absolute())


def test_two_outputs_with_the_same_name_are_refused_before_writing(tmp_path):
    svg = _wide(tmp_path)
    res = cli("export", svg, "--formats", "png", "--sizes", "64,128", "--naming", "{name}{ext}",
              "--out", tmp_path / "o", "-q")
    assert res.exit_code == 1 and "same file" in plain(res.output)
    assert not (tmp_path / "o").exists()


def test_naming_template_cannot_contain_a_folder_climb(tmp_path):
    res = cli("export", _wide(tmp_path), "--formats", "png", "--sizes", "64", "--path", "../../{naming}",
              "--out", tmp_path / "o", "--dry-run", "-q")
    assert res.exit_code == 1 and "outside the output folder" in plain(res.output)
