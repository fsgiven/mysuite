"""--size-by height|width: a size can mean the output's height or width instead of its longest side.

Wide logos (a 876x512 corner bug) asked for "512, 1080, 3000 px high" used to come out 512 px WIDE.
"""
from __future__ import annotations

import shutil

import pytest
from PIL import Image
from pypdf import PdfReader

from tests.helpers import cli, write_svg
from tests.test_export_choices import plain

need = pytest.mark.skipif(not all(shutil.which(t) for t in ("rsvg-convert", "magick")), reason="needs rsvg/magick")

WIDE = '<rect width="876" height="512" fill="#e3000b"/>'


def _export(tmp_path, *args):
    svg = write_svg(tmp_path / "bug.svg", WIDE, width=876, height=512)
    res = cli("export", svg, "--formats", "png,pdf,svg", "--sizes", "100,512,1080", "--out", tmp_path / "o", "-q", *args)
    assert res.exit_code == 0, res.stdout
    return tmp_path / "o" / "bug"


def _svg_dims(path):
    import re
    tag = re.search(r"<svg[^>]*>", path.read_text()).group(0)
    return tuple(round(float(re.search(rf'\b{a}="([\d.]+)', tag).group(1))) for a in ("width", "height"))


@need
def test_default_is_still_the_longest_side(tmp_path):
    out = _export(tmp_path)
    w, h = Image.open(out / "png" / "rgb" / "bug_512.png").size
    assert w == 512 and abs(h - 299.25) <= 1


@need
def test_height_sets_the_height_in_png_svg_and_pdf(tmp_path):
    out = _export(tmp_path, "--size-by", "height", "--vector-size", "100,512,1080")
    for s in (100, 512, 1080):
        w, h = Image.open(out / "png" / "rgb" / f"bug_{s}.png").size
        assert h == s and abs(w - s * 876 / 512) <= 1, (s, w, h)
        sw, sh = _svg_dims(out / "svg" / "rgb" / f"bug_{s}.svg")
        assert sh == s and abs(sw - s * 876 / 512) <= 1
        box = PdfReader(out / "pdf" / "rgb" / f"bug_{s}.pdf").pages[0].mediabox
        assert abs(float(box.height) - s) <= 0.5 and abs(float(box.width) - s * 876 / 512) <= 1   # 72 dpi: 1 px = 1 pt


@need
def test_width_sets_the_width(tmp_path):
    out = _export(tmp_path, "--size-by", "width")
    w, h = Image.open(out / "png" / "rgb" / "bug_100.png").size
    assert w == 100 and abs(h - 58.45) <= 1


@need
def test_height_at_native_size_keeps_the_svg_drawing(tmp_path):
    from mysuite.config import ToolPaths
    from mysuite.vector.verify import renders_same

    out = _export(tmp_path, "--size-by", "height", "--vector-size", "512")
    text = (out / "svg" / "rgb" / "bug_512.svg").read_text()
    assert _svg_dims(out / "svg" / "rgb" / "bug_512.svg") == (876, 512) and "<rect" in text   # the source, not a re-render
    assert renders_same((tmp_path / "bug.svg").read_text(), text, ToolPaths())[0]


def test_bad_size_by_is_rejected(tmp_path):
    svg = write_svg(tmp_path / "bug.svg", WIDE, width=876, height=512)
    res = cli("export", svg, "--formats", "png", "--size-by", "diagonal", "--out", tmp_path / "o", "-q")
    assert res.exit_code == 2                              # a declared choice: Click's usage error
    shown = plain(res.output)
    assert "--size-by" in shown and all(f"'{v}'" in shown for v in ("longest", "height", "width"))


@need
def test_size_by_from_config(tmp_path):
    cfg = tmp_path / "mysuite.toml"
    cfg.write_text('[export]\nsize_by = "height"\n')
    svg = write_svg(tmp_path / "bug.svg", WIDE, width=876, height=512)
    res = cli("export", svg, "--formats", "png", "--sizes", "100", "--out", tmp_path / "o", "--config", cfg, "-q")
    assert res.exit_code == 0, res.stdout
    assert Image.open(next((tmp_path / "o").rglob("*.png"))).size[1] == 100
