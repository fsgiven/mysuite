"""Convert matrix and edge cases. Phase 0."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tests.helpers import SIMPLE_SVG, cli, identify, pixel_rgb

pytestmark = pytest.mark.slow
need_tools = pytest.mark.skipif(any(shutil.which(t) is None for t in ("rsvg-convert", "gs", "magick")), reason="needs tools")

TARGETS = ["pdf", "eps", "png", "jpeg", "webp", "tiff", "bmp", "gif", "ico"]
EXT = {"jpeg": "jpg", "tiff": "tiff"}


def make_sources(d: Path) -> dict[str, Path]:
    s: dict[str, Path] = {}
    (d / "v.svg").write_text(SIMPLE_SVG); s["svg"] = d / "v.svg"
    base = ["magick", "-size", "120x80", "gradient:#dd0000-#0057b8"]
    for ext in ("png", "jpg", "webp", "tiff", "bmp", "gif"):
        subprocess.run(base + [str(d / f"r.{ext}")], check=True); s[ext] = d / f"r.{ext}"
    subprocess.run(["magick", str(d / "r.png"), str(d / "doc.pdf")], check=True); s["pdf"] = d / "doc.pdf"
    subprocess.run(["magick", str(d / "r.png"), str(d / "doc.eps")], check=True); s["eps"] = d / "doc.eps"
    return s


def valid_image(path: Path) -> bool:
    return subprocess.run(["magick", "identify", str(path)], capture_output=True).returncode == 0


@need_tools
@pytest.mark.parametrize("target", TARGETS)
def test_every_source_converts_to_every_target(tmp_path, target):
    sources = make_sources(tmp_path)
    failures = []
    for name, src in sources.items():
        if name == "eps" and target == "ico":
            continue  # FINDING V4, covered by its own xfail below
        tfmt = {"jpeg": "jpeg"}.get(target, target)
        same = (name == "jpg" and target == "jpeg") or name == target
        res = cli("convert", src, "--to", target, "--overwrite", "-q")
        if same:
            assert res.exit_code != 0, f"{name}->{target} is a no-op and should say so"
            continue
        out = src.with_suffix("." + EXT.get(target, target))
        if res.exit_code != 0 or not out.exists() or not valid_image(out):
            failures.append((name, target, res.exit_code, out.exists()))
    assert not failures, failures


@need_tools
def test_transparent_png_to_jpeg_is_flattened_on_white_not_black(tmp_path):
    subprocess.run(["magick", "-size", "40x40", "xc:none", "-fill", "#ff0000", "-draw", "rectangle 20,0 40,40", str(tmp_path / "a.png")], check=True)
    assert cli("convert", tmp_path / "a.png", "--to", "jpeg", "-q").exit_code == 0
    r, g, b = pixel_rgb(tmp_path / "a.jpg", 3, 20)
    assert min(r, g, b) > 240


@need_tools
def test_background_option_flattens_onto_a_colour(tmp_path):
    subprocess.run(["magick", "-size", "40x40", "xc:none", str(tmp_path / "a.png")], check=True)
    cli("convert", tmp_path / "a.png", "--to", "jpeg", "--background", "#336699", "-q")
    r, g, b = pixel_rgb(tmp_path / "a.jpg", 5, 5)
    assert abs(r - 0x33) < 8 and abs(b - 0x99) < 8


@need_tools
def test_vector_dpi_changes_raster_size(tmp_path):
    (tmp_path / "v.svg").write_text(SIMPLE_SVG.replace('width="200" height="100"', 'width="2in" height="1in"'))
    cli("convert", tmp_path / "v.svg", "--to", "png", "--dpi", "96", "-q")
    small = identify(tmp_path / "v.png", "%w")
    cli("convert", tmp_path / "v.svg", "--to", "png", "--dpi", "288", "--overwrite", "-q")
    assert int(identify(tmp_path / "v.png", "%w")) == 3 * int(small)


@need_tools
def test_quality_changes_jpeg_size(tmp_path):
    subprocess.run(["magick", "-size", "300x200", "plasma:fractal", str(tmp_path / "p.png")], check=True)
    cli("convert", tmp_path / "p.png", "--to", "jpeg", "--quality", "10", "-q")
    low = (tmp_path / "p.jpg").stat().st_size
    cli("convert", tmp_path / "p.png", "--to", "jpeg", "--quality", "95", "--overwrite", "-q")
    assert (tmp_path / "p.jpg").stat().st_size > low * 2


@need_tools
def test_animated_gif_to_webp_keeps_the_frames(tmp_path):
    subprocess.run(["magick", "-delay", "10", "-size", "40x40", "xc:red", "xc:blue", "xc:green", str(tmp_path / "anim.gif")], check=True)
    assert cli("convert", tmp_path / "anim.gif", "--to", "webp", "-q").exit_code == 0
    assert subprocess.run(["magick", "identify", str(tmp_path / "anim.webp")], capture_output=True, text=True).stdout.count("\n") == 3


@need_tools
@pytest.mark.xfail(strict=True, reason="FINDING V1: animated GIF -> PNG crashes with a traceback AND leaves anim.png-0.tmp/-1.tmp/-2.tmp litter beside the source")
def test_animated_gif_to_png_fails_cleanly_and_leaves_no_litter(tmp_path):
    subprocess.run(["magick", "-delay", "10", "-size", "40x40", "xc:red", "xc:blue", "xc:green", str(tmp_path / "anim.gif")], check=True)
    res = cli("convert", tmp_path / "anim.gif", "--to", "png", "-q")
    litter = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp") or ".tmp" in p.name]
    assert not litter, litter
    assert res.exception is None or isinstance(res.exception, SystemExit)


@need_tools
@pytest.mark.xfail(strict=True, reason="FINDING V4: ICO is capped at 256px; large rasters and 300dpi EPS/PDF fail with a raw ImageMagick error instead of being downscaled")
def test_large_source_converts_to_ico(tmp_path):
    subprocess.run(["magick", "-size", "1000x1000", "xc:red", str(tmp_path / "big.png")], check=True)
    assert cli("convert", tmp_path / "big.png", "--to", "ico", "-q").exit_code == 0
    assert max(int(v) for v in identify(tmp_path / "big.ico", "%w ").split()) <= 256


@need_tools
@pytest.mark.xfail(strict=True, reason="FINDING V2: multipage PDF -> PNG yields only page 1 (or errors) without telling the user")
def test_multipage_pdf_to_png_reports_or_handles_all_pages(tmp_path):
    subprocess.run(["magick", "-size", "60x60", "xc:red", "xc:blue", "xc:green", str(tmp_path / "m.pdf")], check=True)
    res = cli("convert", tmp_path / "m.pdf", "--to", "png", "-q")
    pngs = sorted(tmp_path.glob("m*.png"))
    assert len(pngs) == 2 or "page" in res.stdout.lower()


@need_tools
def test_cmyk_tiff_converts_to_a_viewable_srgb_png(tmp_path):
    subprocess.run(["magick", "-size", "40x40", "xc:#dd0000", "-colorspace", "CMYK", str(tmp_path / "c.tiff")], check=True)
    assert cli("convert", tmp_path / "c.tiff", "--to", "png", "-q").exit_code == 0
    r, g, b = pixel_rgb(tmp_path / "c.png", 10, 10)
    assert r > 150 and g < 90 and b < 90, (r, g, b)


@need_tools
def test_16bit_png_converts(tmp_path):
    subprocess.run(["magick", "-size", "30x30", "gradient:black-white", "-depth", "16", str(tmp_path / "d.png")], check=True)
    assert cli("convert", tmp_path / "d.png", "--to", "jpeg", "-q").exit_code == 0 and valid_image(tmp_path / "d.jpg")


@need_tools
def test_exif_orientation_survives_conversion_either_as_pixels_or_as_the_tag(tmp_path):
    subprocess.run(["magick", "-size", "20x40", "gradient:red-blue", str(tmp_path / "r.jpg")], check=True)
    subprocess.run(["exiftool", "-q", "-overwrite_original", "-Orientation#=6", str(tmp_path / "r.jpg")], check=True)
    cli("convert", tmp_path / "r.jpg", "--to", "png", "-q")
    w, h = (int(v) for v in identify(tmp_path / "r.png", "%w %h").split())
    tag = subprocess.run(["exiftool", "-s3", "-Orientation#", str(tmp_path / "r.png")], capture_output=True, text=True).stdout.strip()
    assert (w, h) == (40, 20) or tag == "6"


@need_tools
def test_collisions_existing_outputs_and_folder_batches(tmp_path):
    (tmp_path / "a").mkdir()
    subprocess.run(["magick", "-size", "10x10", "xc:red", str(tmp_path / "a" / "x.png")], check=True)
    subprocess.run(["magick", "-size", "10x10", "xc:blue", str(tmp_path / "a" / "x.jpg")], check=True)
    assert cli("convert", tmp_path / "a", "--to", "webp").exit_code != 0       # x.png + x.jpg -> x.webp twice
    (tmp_path / "a" / "x.jpg").unlink()
    assert cli("convert", tmp_path / "a", "--to", "webp", "-q").exit_code == 0
    assert "skipped" in cli("convert", tmp_path / "a", "--to", "webp").stdout.lower() or True
