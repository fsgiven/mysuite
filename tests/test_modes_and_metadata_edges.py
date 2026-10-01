"""Odd colour modes through compress/enhance, and metadata edge files. Phase 0."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tests.helpers import cli, identify, pixel_rgb

pytestmark = pytest.mark.slow
need = pytest.mark.skipif(any(shutil.which(t) is None for t in ("magick", "exiftool")), reason="needs magick+exiftool")


def mk(path: Path, *args: str) -> Path:
    subprocess.run(["magick", "-size", "80x60", *args, str(path)], check=True)
    return path


# ------------------------------------------------------------- enhance / compress
@need
@pytest.mark.parametrize("name,args", [
    ("cmyk.tiff", ["xc:#dd0000", "-colorspace", "CMYK"]),
    ("sixteen.png", ["gradient:black-white", "-depth", "16"]),
    ("gray.png", ["gradient:black-white", "-colorspace", "Gray"]),
    ("palette.png", ["gradient:red-blue", "-colors", "8"]),
    ("alpha.png", ["xc:none", "-fill", "red", "-draw", "circle 40,30 40,5"]),
])
def test_enhance_accepts_unusual_colour_modes(tmp_path, name, args):
    src = mk(tmp_path / name, *args)
    res = cli("enhance", "run", src, "--scale", "1", "--backend", "classical", "-q")
    assert res.exit_code == 0 and res.exception is None, (name, res.stdout[-200:])
    assert identify(tmp_path / (src.stem + "_enhanced.png"), "%wx%h") == "80x60"


@need
def test_enhance_cmyk_source_comes_out_with_plausible_srgb_colour(tmp_path):
    src = mk(tmp_path / "c.tiff", "xc:#dd0000", "-colorspace", "CMYK")
    cli("enhance", "run", src, "--scale", "1", "--denoise", "0", "--sharpen", "0", "--no-auto-white-balance", "--backend", "classical", "-q")
    r, g, b = pixel_rgb(tmp_path / "c_enhanced.png", 10, 10)
    assert r > 150 and g < 100 and b < 100, (r, g, b)


@need
@pytest.mark.xfail(strict=True, reason="FINDING Q1: 16-bit sources are silently reduced to 8-bit by enhance with no mention")
def test_enhance_preserves_or_reports_16bit_depth(tmp_path):
    src = mk(tmp_path / "s.png", "gradient:black-white", "-depth", "16")
    res = cli("enhance", "run", src, "--scale", "1", "--backend", "classical")
    assert identify(tmp_path / "s_enhanced.png", "%z") == "16" or "8-bit" in res.stdout.lower()


@need
@pytest.mark.parametrize("codec,ext", [("mozjpeg", "jpg"), ("webp", "webp"), ("oxipng", "png"), ("pngquant", "png"), ("gifsicle", "gif")])
def test_compress_handles_cmyk_and_16bit_and_alpha_sources(tmp_path, codec, ext):
    for name, args in (("c.tiff", ["xc:#dd0000", "-colorspace", "CMYK"]), ("d.png", ["gradient:black-white", "-depth", "16"]),
                       ("a.png", ["xc:none", "-fill", "red", "-draw", "circle 40,30 40,5"])):
        if codec == "webp" and name == "c.tiff":
            continue  # FINDING Q2, own xfail below
        src = mk(tmp_path / name, *args)
        res = cli("compress", src, "--codec", codec, "-q")
        assert res.exit_code == 0 and res.exception is None, (codec, name, res.stdout[-200:])
        assert subprocess.run(["magick", "identify", str(tmp_path / f"{src.stem}_compressed.{ext}")], capture_output=True).returncode == 0


@need
@pytest.mark.xfail(strict=True, reason="FINDING Q2: compress --codec webp fails on a CMYK TIFF (handed to cwebp as-is) instead of converting to RGB first")
def test_compress_webp_accepts_a_cmyk_tiff(tmp_path):
    src = mk(tmp_path / "c.tiff", "xc:#dd0000", "-colorspace", "CMYK")
    assert cli("compress", src, "--codec", "webp", "-q").exit_code == 0


@need
def test_compress_webp_keeps_alpha(tmp_path):
    src = mk(tmp_path / "a.png", "xc:none", "-fill", "red", "-draw", "circle 40,30 40,5")
    cli("compress", src, "--codec", "webp", "-q")
    a = subprocess.run(["magick", str(tmp_path / "a_compressed.webp"), "-format", "%[fx:p{1,1}.a]", "info:"], capture_output=True, text=True).stdout
    assert float(a) < 0.1


# --------------------------------------------------------------------- metadata
@need
def test_strip_and_randomize_on_png_text_chunks_and_xmp(tmp_path):
    src = mk(tmp_path / "t.png", "xc:red", "-set", "comment", "made by Frank", "-set", "Author", "Frank")
    subprocess.run(["exiftool", "-q", "-overwrite_original", "-XMP-dc:Creator=Frank", "-Comment=host=Franks-Mac", str(src)], check=True)
    cli("metadata", "strip", src, "-q")
    raw = (tmp_path / "t_stripped.png").read_bytes()
    assert b"Frank" not in raw and b"Franks-Mac" not in raw


@need
def test_strip_animated_webp_keeps_all_frames(tmp_path):
    subprocess.run(["magick", "-delay", "10", "-size", "30x30", "xc:red", "xc:blue", str(tmp_path / "a.webp")], check=True)
    subprocess.run(["exiftool", "-q", "-overwrite_original", "-Artist=Frank", str(tmp_path / "a.webp")], check=False)
    res = cli("metadata", "strip", tmp_path / "a.webp", "-q")
    assert res.exit_code == 0
    assert subprocess.run(["magick", "identify", str(tmp_path / "a_stripped.webp")], capture_output=True, text=True).stdout.count("\n") == 2


@need
def test_strip_file_without_extension_or_with_wrong_extension_fails_cleanly(tmp_path):
    p = tmp_path / "actually_png.jpg"
    mk(p.with_suffix(".png"), "xc:red").rename(p)
    res = cli("metadata", "strip", p, "-q")
    assert res.exception is None or isinstance(res.exception, SystemExit)


@need
def test_randomize_twice_gives_different_decoys_often(tmp_path):
    src = mk(tmp_path / "p.jpg", "gradient:red-blue")
    seen = set()
    for i in range(12):
        cli("metadata", "randomize", src, "--overwrite", "-q")
        seen.add(subprocess.run(["exiftool", "-s3", "-Model", str(tmp_path / "p_randomized.jpg")], capture_output=True, text=True).stdout.strip())
    assert len(seen) >= 3
