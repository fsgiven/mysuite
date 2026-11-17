"""Exported SVG = the (recoloured / variant) source, optimised - never a cairo re-render (WS-A item 3).

The re-render wrote colours as rgb(86.666667%, 0%, 0%), dropped every id and group, and turned shapes into paths.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from mysuite.config import ToolPaths
from mysuite.vector.verify import renders_same
from tests.helpers import cli

need = pytest.mark.skipif(not all(shutil.which(t) for t in ("rsvg-convert", "magick", "gs")), reason="needs rsvg/magick/gs")

SOURCE = """<?xml version="1.0"?>
<!-- Generator: Illustrator -->
<svg xmlns="http://www.w3.org/2000/svg" width="333" height="127" viewBox="0 0 333 127"><metadata>editor junk</metadata>
<g id="Logo"><rect id="bg" width="333" height="127" fill="#1d1d1b"/>
<path id="mark" d="M20.123456 20 L313 20 L313 107 Z" fill="#dd0000"/></g></svg>
"""


def _export(tmp_path: Path, *args: str, source: str = SOURCE) -> Path:
    svg = tmp_path / "logo.svg"
    svg.write_text(source)
    res = cli("export", svg, "--formats", "svg", "--out", tmp_path / "o", "-q", *args)
    assert res.exit_code == 0, res.stdout
    return tmp_path / "o" / "logo"


@need
def test_svg_keeps_ids_shapes_and_hex_colours(tmp_path):
    text = (_export(tmp_path) / "svg" / "rgb" / "logo.svg").read_text()
    assert "rgb(" not in text and "%" not in text
    assert 'id="Logo"' in text and 'id="bg"' in text and 'id="mark"' in text
    assert "<rect" in text and 'fill="#dd0000"' in text and 'fill="#1d1d1b"' in text


@need
def test_svg_is_optimised(tmp_path):
    text = (_export(tmp_path) / "svg" / "rgb" / "logo.svg").read_text()
    assert "metadata" not in text and "Illustrator" not in text and "<!--" not in text
    assert "20.123456" not in text and "20.123" in text


@need
def test_svg_looks_the_same_as_the_source(tmp_path):
    text = (_export(tmp_path) / "svg" / "rgb" / "logo.svg").read_text()
    assert renders_same(SOURCE, text, ToolPaths())[0]


@need
def test_recolour_and_variant_reach_the_svg_source(tmp_path):
    out = _export(tmp_path, "--recolor", "#dd0000=#0057b8", "--variants", "default,mono-white")
    assert 'fill="#0057b8"' in (out / "svg" / "rgb" / "logo.svg").read_text()
    white = (out / "mono-white" / "svg" / "rgb" / "logo_mono-white.svg").read_text()
    assert 'id="mark"' in white and "rgb(" not in white


@need
def test_a_chosen_size_sets_width_and_height_only(tmp_path):
    text = (_export(tmp_path, "--vector-size", "512") / "svg" / "rgb" / "logo_512.svg").read_text()
    root = re.search(r"<svg[^>]*>", text).group(0)
    assert 'width="512"' in root and 'viewBox="0 0 333 127"' in root
    assert abs(float(re.search(r'height="([\d.]+)"', root).group(1)) - 512 * 127 / 333) < 0.01
    assert 'id="mark"' in text and "rgb(" not in text


@need
def test_a_chosen_physical_size_keeps_its_unit(tmp_path):
    text = (_export(tmp_path, "--vector-size", "100mm") / "svg" / "rgb" / "logo_100mm.svg").read_text()
    root = re.search(r"<svg[^>]*>", text).group(0)
    assert 'width="100mm"' in root and re.search(r'height="[\d.]+mm"', root)


@need
def test_a_chosen_size_adds_the_missing_viewbox(tmp_path):
    source = '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="60"><rect width="120" height="60" fill="#336699"/></svg>'
    text = (_export(tmp_path, "--vector-size", "240", source=source) / "svg" / "rgb" / "logo_240.svg").read_text()
    root = re.search(r"<svg[^>]*>", text).group(0)
    assert 'viewBox="0 0 120 60"' in root and 'width="240"' in root and 'height="120"' in root


def test_long_hex_only_touches_colours():
    from mysuite.export.renderer import long_hex

    text = '<g fill="#d00" style="stroke:#fff;opacity:.5"><use href="#abc"/><path id="a" stroke="#1D1D1B"/></g>'
    assert long_hex(text) == '<g fill="#dd0000" style="stroke:#ffffff;opacity:.5"><use href="#abc"/><path id="a" stroke="#1D1D1B"/></g>'
