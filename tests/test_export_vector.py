"""Vector output once (WS-A item 2): SVG/PDF/EPS are written once per variant x colour space, at the artwork's
own size (1 px = 1 pt) unless --vector-size asks for other sizes, and the EPS bounding box is the PDF page."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest
from pypdf import PdfReader

from tests.helpers import cli, write_svg

need = pytest.mark.skipif(not all(shutil.which(t) for t in ("rsvg-convert", "magick", "gs")), reason="needs rsvg/magick/gs")

# a 333 x 127 artboard with the red shape inset by 20: the artboard (not the ink) is the logo's size
INSET = '<rect x="20" y="20" width="293" height="87" fill="#dd0000"/>'


def _logo(tmp_path: Path, **kw) -> Path:
    return write_svg(tmp_path / "logo.svg", INSET, **({"width": 333, "height": 127} | kw))


def _files(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


def _eps_bbox(path: Path) -> tuple[float, ...]:
    head = path.read_bytes()[:4000].decode("latin-1")
    return tuple(float(v) for v in re.search(r"%%HiResBoundingBox:\s*([\d.\s-]+)", head).group(1).split())


@need
def test_vectors_are_written_once_not_per_pixel_size(tmp_path):
    res = cli("export", _logo(tmp_path), "--formats", "png,pdf,eps,svg", "--sizes", "64,512", "--out", tmp_path / "o", "-q")
    assert res.exit_code == 0, res.stdout
    assert _files(tmp_path / "o") == [
        "logo/eps/rgb/logo.eps", "logo/pdf/rgb/logo.pdf", "logo/png/rgb/logo_512.png", "logo/png/rgb/logo_64.png",
        "logo/svg/rgb/logo.svg",
    ]


@need
def test_pdf_is_the_artwork_size_one_px_one_pt(tmp_path):
    cli("export", _logo(tmp_path), "--formats", "pdf", "--dpi", "300", "--out", tmp_path / "o", "-q")
    box = PdfReader(tmp_path / "o" / "logo" / "pdf" / "rgb" / "logo.pdf").pages[0].mediabox
    assert (round(float(box.width), 2), round(float(box.height), 2)) == (333, 127)


@need
def test_a_millimetre_artboard_stays_millimetres(tmp_path):
    svg = tmp_path / "logo.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="50mm" viewBox="0 0 100 50">'
                   '<rect width="100" height="50" fill="#dd0000"/></svg>')
    cli("export", svg, "--formats", "pdf", "--out", tmp_path / "o", "-q")
    box = PdfReader(tmp_path / "o" / "logo" / "pdf" / "rgb" / "logo.pdf").pages[0].mediabox
    assert abs(float(box.width) - 100 * 72 / 25.4) < 0.5 and abs(float(box.height) - 50 * 72 / 25.4) < 0.5


@need
def test_eps_bounding_box_is_the_pdf_page(tmp_path):
    res = cli("export", _logo(tmp_path), "--formats", "pdf,eps", "--profiles", "rgb,cmyk", "--out", tmp_path / "o", "-q")
    assert res.exit_code == 0, res.stdout
    for space in ("rgb", "cmyk"):
        box = PdfReader(tmp_path / "o" / "logo" / "pdf" / space / "logo.pdf").pages[0].mediabox
        eps = _eps_bbox(tmp_path / "o" / "logo" / "eps" / space / "logo.eps")
        assert eps == pytest.approx((float(box.left), float(box.bottom), float(box.right), float(box.top)), abs=0.01), space


@need
def test_chosen_vector_sizes(tmp_path):
    res = cli("export", _logo(tmp_path), "--formats", "pdf", "--vector-size", "512,100mm,artwork",
              "--out", tmp_path / "o", "-q")
    assert res.exit_code == 0, res.stdout
    pdfs = tmp_path / "o" / "logo" / "pdf" / "rgb"
    assert sorted(p.name for p in pdfs.iterdir()) == ["logo.pdf", "logo_100mm.pdf", "logo_512.pdf"]
    assert round(float(PdfReader(pdfs / "logo_512.pdf").pages[0].mediabox.width)) == 512
    assert abs(float(PdfReader(pdfs / "logo_100mm.pdf").pages[0].mediabox.width) - 100 * 72 / 25.4) < 0.5


@need
def test_one_vector_per_variant_and_colour_space(tmp_path):
    res = cli("export", _logo(tmp_path), "--formats", "pdf,png", "--profiles", "rgb,cmyk", "--sizes", "64,128",
              "--variants", "default,invert", "--out", tmp_path / "o", "--json")
    assert res.exit_code == 0, res.stdout
    pdfs = [i for i in json.loads(res.stdout)["items"] if i["format"] == "pdf"]
    assert len(pdfs) == 4 and {i["size"] for i in pdfs} == {"artwork"}


def test_dry_run_lists_vectors_once(tmp_path):
    res = cli("export", _logo(tmp_path), "--formats", "png,svg", "--sizes", "16,32,64", "--out", tmp_path / "o",
              "--dry-run", "--json")
    items = json.loads(res.stdout)["items"]
    assert [i["format"] for i in items].count("svg") == 1


def test_bad_vector_size_is_rejected(tmp_path):
    res = cli("export", _logo(tmp_path), "--formats", "pdf", "--vector-size", "huge", "--out", tmp_path / "o", "-q")
    assert res.exit_code != 0 and "vector-size" in res.stdout


def test_vector_size_from_config(tmp_path):
    cfg = tmp_path / "mysuite.toml"
    cfg.write_text('[export]\nvector_sizes = ["artwork", 256]\n')
    res = cli("export", _logo(tmp_path), "--config", cfg, "--formats", "pdf", "--out", tmp_path / "o", "--dry-run", "--json")
    assert sorted(Path(i["output"]).name for i in json.loads(res.stdout)["items"]) == ["logo.pdf", "logo_256.pdf"]


@need
def test_house_set_reproduced_exactly(tmp_path):
    res = cli("export", _logo(tmp_path), "--formats", "png,pdf,svg,eps", "--profiles", "rgb,cmyk", "--sizes", "512,1080",
              "--variants", "positive=default", "--naming", "20250514_BILD_Logo_{variant}_{size}_{COLORSPACE}{ext}",
              "--path", "{COLORSPACE}/{FORMAT}/{naming}", "--no-name-folder", "--out", tmp_path / "o", "-q")
    assert res.exit_code == 0, res.stdout
    assert _files(tmp_path / "o") == [
        "CMYK/EPS/20250514_BILD_Logo_positive_CMYK.eps",
        "CMYK/PDF/20250514_BILD_Logo_positive_CMYK.pdf",
        "RGB/EPS/20250514_BILD_Logo_positive_RGB.eps",
        "RGB/PDF/20250514_BILD_Logo_positive_RGB.pdf",
        "RGB/PNG/20250514_BILD_Logo_positive_1080_RGB.png",
        "RGB/PNG/20250514_BILD_Logo_positive_512_RGB.png",
        "RGB/SVG/20250514_BILD_Logo_positive_RGB.svg",
    ]
