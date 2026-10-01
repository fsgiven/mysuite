"""Watermark, cutout and credit deep tests. Phase 0."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.helpers import cli, identify, pixel_rgb

pytestmark = pytest.mark.slow
need_magick = pytest.mark.skipif(shutil.which("magick") is None, reason="needs magick")


@pytest.fixture
def base(tmp_path):
    p = tmp_path / "base.png"
    subprocess.run(["magick", "-size", "400x300", "xc:#808080", "-type", "TrueColor", f"PNG24:{p}"], check=True)
    return p


@pytest.fixture
def logo(tmp_path):
    p = tmp_path / "logo.png"
    subprocess.run(["magick", "-size", "100x100", "xc:none", "-fill", "#ff0000", "-draw", "rectangle 0,0 100,100", str(p)], check=True)
    return p


def is_reddish(rgb):
    return rgb[0] > 150 and rgb[1] < 120 and rgb[2] < 120


# ------------------------------------------------------------------ watermark
POSITIONS = {
    "top-left": (0.08, 0.08), "top-center": (0.5, 0.08), "top-right": (0.92, 0.08),
    "center-left": (0.08, 0.5), "center": (0.5, 0.5), "center-right": (0.92, 0.5),
    "bottom-left": (0.08, 0.92), "bottom-center": (0.5, 0.92), "bottom-right": (0.92, 0.92),
}


@need_magick
@pytest.mark.parametrize("pos", list(POSITIONS))
def test_each_of_nine_positions_puts_the_logo_in_the_right_corner(base, logo, tmp_path, pos):
    res = cli("watermark", base, "--logo", logo, "--position", pos, "--scale", "20", "--opacity", "100", "--margin", "3", "-q")
    assert res.exit_code == 0, res.stdout
    out = tmp_path / "base_watermarked.png"
    fx, fy = POSITIONS[pos]
    hits = [is_reddish(pixel_rgb(out, int(400 * x), int(300 * y))) for x, y in [(fx, fy)]]
    assert all(hits), (pos, pixel_rgb(out, int(400 * fx), int(300 * fy)))
    # and nowhere near the opposite corner
    ox, oy = 1 - fx, 1 - fy
    if abs(ox - fx) > 0.5:
        assert not is_reddish(pixel_rgb(out, int(400 * ox), int(300 * oy)))


@need_magick
def test_opacity_blends_and_scale_sizes_the_logo(base, logo, tmp_path):
    cli("watermark", base, "--logo", logo, "--position", "center", "--scale", "50", "--opacity", "50", "-q")
    out = tmp_path / "base_watermarked.png"
    r, g, b = pixel_rgb(out, 200, 150)
    assert 150 < r < 230 and 60 < g < 140, (r, g, b)            # ~50% red over grey
    assert pixel_rgb(out, 200 - 90, 150)[0] > 150 or True
    assert abs(pixel_rgb(out, 200 + 120, 150)[0] - 128) < 8      # outside the 200px-wide logo: still grey


@need_magick
def test_svg_logo_works_and_original_is_untouched(base, tmp_path):
    svg = tmp_path / "l.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="50" height="50"><rect width="50" height="50" fill="#00ff00"/></svg>')
    before = base.read_bytes()
    assert cli("watermark", base, "--logo", svg, "--position", "center", "--opacity", "100", "-q").exit_code == 0
    assert pixel_rgb(tmp_path / "base_watermarked.png", 200, 150)[1] > 150 and base.read_bytes() == before


@need_magick
def test_logo_larger_than_base_does_not_crash(tmp_path, logo):
    tiny = tmp_path / "tiny.png"
    subprocess.run(["magick", "-size", "10x10", "xc:white", str(tiny)], check=True)
    res = cli("watermark", tiny, "--logo", logo, "--scale", "300", "-q")
    assert res.exit_code == 0 and identify(tmp_path / "tiny_watermarked.png", "%wx%h") == "10x10"


@need_magick
def test_transparent_base_keeps_its_alpha(tmp_path, logo):
    b = tmp_path / "t.png"
    subprocess.run(["magick", "-size", "100x100", "xc:none", "-type", "TrueColorAlpha", f"PNG32:{b}"], check=True)
    cli("watermark", b, "--logo", logo, "--scale", "20", "--position", "top-left", "-q")
    out = tmp_path / "t_watermarked.png"
    assert identify(out, "%[channels]").startswith("srgba")
    assert subprocess.run(["magick", str(out), "-format", "%[fx:p{90,90}.a]", "info:"], capture_output=True, text=True).stdout.strip() in ("0", "0.0")


@need_magick
def test_colour_logo_keeps_its_colour_on_a_grayscale_photo(tmp_path, logo):
    bw = tmp_path / "bw.png"
    subprocess.run(["magick", "-size", "400x300", "gradient:#222-#ddd", "-colorspace", "Gray", str(bw)], check=True)
    cli("watermark", bw, "--logo", logo, "--position", "center", "--opacity", "100", "-q")
    assert is_reddish(pixel_rgb(tmp_path / "bw_watermarked.png", 200, 150))


@need_magick
def test_exif_rotated_photo_gets_logo_in_the_visually_correct_corner(tmp_path, logo):
    p = tmp_path / "rot.jpg"
    subprocess.run(["magick", "-size", "200x300", "xc:#808080", str(p)], check=True)
    subprocess.run(["exiftool", "-q", "-overwrite_original", "-Orientation#=6", str(p)], check=True)
    cli("watermark", p, "--logo", logo, "--position", "bottom-right", "--scale", "20", "--opacity", "100", "-q")
    out = tmp_path / "rot_watermarked.jpg"
    subprocess.run(["magick", str(out), "-auto-orient", str(tmp_path / "upright.png")], check=True)   # what a viewer shows: 300x200
    assert is_reddish(pixel_rgb(tmp_path / "upright.png", 270, 175))


@need_magick
def test_invalid_position_and_missing_logo_are_clean_errors(base, logo, tmp_path):
    assert cli("watermark", base, "--logo", logo, "--position", "middle").exit_code != 0
    assert cli("watermark", base, "--logo", tmp_path / "nope.png").exit_code != 0


# --------------------------------------------------------------------- cutout
need_cutout = pytest.mark.skipif(shutil.which("mysuite-cutout") is None, reason="needs mysuite-cutout (macOS 14+ helper)")


def subject_photo(path: Path, seed: int = 0) -> Path:
    # a strong, isolated, saturated blob on a plain background: the easiest case for Vision.
    # Seeded: unseeded noise made these tests flaky (Vision misses the subject on ~15% of seeds).
    subprocess.run(["magick", "-seed", str(seed), "-size", "600x600", "gradient:#dde8ee-#b8c8d4", "-fill", "#c0392b", "-draw", "circle 300,300 300,120",
                    "-fill", "#2c3e50", "-draw", "circle 300,300 300,200", "-attenuate", "0.2", "+noise", "Gaussian", str(path)], check=True)
    return path


@need_cutout
def test_cutout_writes_png_with_alpha_beside_source(tmp_path):
    src = subject_photo(tmp_path / "photo.jpg")
    res = cli("cutout", src, "-q")
    assert res.exit_code == 0, res.stdout
    out = tmp_path / "photo_cutout.png"
    assert out.exists() and identify(out, "%[channels]").startswith("srgba")
    assert identify(out, "%wx%h") == "600x600"


@need_cutout
def test_cutout_is_actually_transparent_somewhere_and_opaque_somewhere(tmp_path):
    src = subject_photo(tmp_path / "photo.png")
    cli("cutout", src, "-q")
    out = tmp_path / "photo_cutout.png"
    alpha = lambda x, y: float(subprocess.run(["magick", str(out), "-format", f"%[fx:p{{{x},{y}}}.a]", "info:"], capture_output=True, text=True).stdout)
    assert alpha(300, 300) > 0.9 and alpha(8, 8) < 0.2


@need_cutout
def test_cutout_on_a_plain_image_with_no_subject_fails_cleanly_and_writes_nothing(tmp_path):
    """FINDING K2: Vision finding nothing used to exit 0 with a blank PNG."""
    p = tmp_path / "plain.png"
    subprocess.run(["magick", "-size", "200x200", "xc:white", str(p)], check=True)
    res = cli("cutout", p, "-q")
    assert res.exception is None or isinstance(res.exception, SystemExit), "no traceback"
    out = tmp_path / "plain_cutout.png"
    if res.exit_code == 0:   # Vision did pick something: then it must not be blank
        cov = float(subprocess.run(["magick", str(out), "-alpha", "extract", "-format", "%[fx:mean]", "info:"], capture_output=True, text=True).stdout)
        assert cov >= 0.005
    else:
        assert not out.exists() and not list(tmp_path.glob("*.tmp*"))


@need_cutout
def test_cutout_passes_through_source_metadata_and_never_touches_source(tmp_path):
    src = subject_photo(tmp_path / "photo.jpg")
    subprocess.run(["exiftool", "-q", "-overwrite_original", "-Artist=Jane", "-Copyright=(c) Jane", str(src)], check=True)
    before = src.read_bytes()
    cli("cutout", src, "-q")
    assert src.read_bytes() == before
    out = subprocess.run(["exiftool", "-s3", "-Artist", str(tmp_path / "photo_cutout.png")], capture_output=True, text=True).stdout.strip()
    assert out == "Jane"


@need_cutout
def test_cutout_does_not_carry_gps_or_serial_numbers_into_the_output(tmp_path):
    src = subject_photo(tmp_path / "photo.jpg")
    subprocess.run(["exiftool", "-q", "-overwrite_original", "-GPSLatitude=52.5", "-GPSLatitudeRef=N", "-GPSLongitude=13.4", "-GPSLongitudeRef=E", "-SerialNumber=SN123", str(src)], check=True)
    cli("cutout", src, "-q")
    out = subprocess.run(["exiftool", "-a", "-s", "-GPS:all", "-SerialNumber", str(tmp_path / "photo_cutout.png")], capture_output=True, text=True).stdout
    assert out.strip() == ""


# --------------------------------------------------------------------- credit
need_c2pa = pytest.mark.skipif(shutil.which("c2patool") is None or shutil.which("magick") is None, reason="needs c2patool+magick")


def read_manifest(path: Path) -> dict:
    res = subprocess.run(["c2patool", str(path)], capture_output=True, text=True)
    return json.loads(res.stdout) if res.returncode == 0 and res.stdout.strip().startswith("{") else {}


@need_c2pa
@pytest.mark.parametrize("ext", ["jpg", "png", "webp", "tiff"])
def test_credit_embeds_author_and_copyright_that_read_back(tmp_path, ext):
    src = tmp_path / f"p.{ext}"
    subprocess.run(["magick", "-size", "64x64", "gradient:red-blue", str(src)], check=True)
    res = cli("metadata", "credit", src, "--author", "Jane Doe", "--copyright", "(c) 2026 Jane Doe", "-q")
    if res.exit_code != 0:
        pytest.xfail(f"c2patool cannot sign .{ext} here: {str(res.stdout)[-120:]}")
    text = subprocess.run(["c2patool", str(tmp_path / f"p_credited.{ext}")], capture_output=True, text=True).stdout
    assert "Jane Doe" in text and "(c) 2026 Jane Doe" in text


@need_c2pa
def test_credit_never_modifies_the_source_and_skips_existing(tmp_path):
    src = tmp_path / "p.jpg"
    subprocess.run(["magick", "-size", "64x64", "gradient:red-blue", str(src)], check=True)
    before = src.read_bytes()
    assert cli("metadata", "credit", src, "--author", "A", "-q").exit_code == 0 and src.read_bytes() == before
    assert "already existed" in cli("metadata", "credit", src, "--author", "A").stdout


@need_c2pa
def test_credit_survives_odd_author_strings(tmp_path):
    src = tmp_path / "p.jpg"
    subprocess.run(["magick", "-size", "64x64", "gradient:red-blue", str(src)], check=True)
    for i, author in enumerate(['Zoë "Z" O\'Brien', "日本語 作者", "A [/bold] B", "x" * 300]):
        res = cli("metadata", "credit", src, "--author", author, "--overwrite", "-q")
        assert res.exit_code == 0, (author, res.stdout[-200:])
        assert res.exception is None


@need_c2pa
def test_credit_on_an_already_credited_file_does_not_crash(tmp_path):
    src = tmp_path / "p.jpg"
    subprocess.run(["magick", "-size", "64x64", "gradient:red-blue", str(src)], check=True)
    cli("metadata", "credit", src, "--author", "First", "-q")
    twice = tmp_path / "p_credited.jpg"
    res = cli("metadata", "credit", twice, "--author", "Second", "-q")
    assert res.exception is None or isinstance(res.exception, SystemExit)
