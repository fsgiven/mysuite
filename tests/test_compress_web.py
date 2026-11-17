"""WS-C1 item 2: "Save for web": --max-kb, --max-size, and web metadata (GPS/serials out, copyright kept, sRGB in)."""
from __future__ import annotations

import io
import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageCms, ImageFilter

from mysuite.cli import app
from mysuite.compress.compress import compress_file
from mysuite.config import ToolPaths
from tests.helpers import runner

_TOOLS = {"mozjpeg": "cjpeg", "webp": "cwebp", "avif": "avifenc", "oxipng": "oxipng", "pngquant": "pngquant"}


def _have(codec: str) -> bool:
    attr = _TOOLS[codec]
    return Path(getattr(ToolPaths(), attr)).exists() or shutil.which(attr) is not None


def need(codec: str):
    return pytest.mark.skipif(not _have(codec), reason=f"requires the {codec} encoder")


def _photo(path: Path, size=(1200, 900), **save) -> Path:
    """Smooth gradients plus fine noise: compresses like a photo (quality changes the size a lot)."""
    w, h = size
    y, x = np.mgrid[0:h, 0:w]
    rgb = np.dstack([x * 255 / w, y * 255 / h, (x + y) * 127 / (w + h) + 64])
    rgb += np.random.default_rng(3).normal(0, 18, rgb.shape)
    img = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.6))
    img.save(path, **save)
    return path


def _private_exif() -> bytes:
    ex = Image.Exif()
    ex[0x8298] = "© 2026 Jane Example".encode("utf-8")     # Copyright (UTF-8, as Lightroom/exiftool write it)
    ex[0x013B] = "Jane Example"                            # Artist
    ex[0x0110] = "PhoneCam 12"             # Model
    gps = ex.get_ifd(0x8825)
    gps[1], gps[2] = "N", (12.0, 20.0, 42.0)
    ex.get_ifd(0x8769)[0xA431] = "SERIAL-0001"   # BodySerialNumber
    return ex.tobytes()


_SRGB = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def _srgb_tagged(im: Image.Image) -> bool:
    """An sRGB ICC profile, or (PNG) the 13-byte sRGB chunk, which Pillow reports as info["srgb"]."""
    icc = im.info.get("icc_profile")
    if not icc:
        return im.format == "PNG" and "srgb" in im.info
    return "srgb" in ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(io.BytesIO(icc))).lower()


# --------------------------------------------------------------------------- --max-kb
@need("mozjpeg")
def test_max_kb_fits_under_the_limit_with_the_highest_quality_that_does(tmp_path):
    src = _photo(tmp_path / "photo.png")
    out = compress_file(src, "mozjpeg", tools=ToolPaths(), max_kb=120)
    assert out.status == "written"
    assert out.output_path.stat().st_size <= 120_000
    assert 20 <= out.quality < 95
    one_up = compress_file(src, "mozjpeg", tools=ToolPaths(), quality=out.quality + 1, allow_larger=True,
                           output_path=tmp_path / "probe.jpg")
    assert one_up.bytes_after > 120_000                    # the search found the best quality that fits


@need("webp")
def test_max_kb_works_for_webp_too(tmp_path):
    out = compress_file(_photo(tmp_path / "photo.png"), "webp", tools=ToolPaths(), max_kb=60)
    assert out.status == "written" and out.output_path.stat().st_size <= 60_000


@need("mozjpeg")
def test_max_kb_that_cannot_be_reached_fails_with_a_hint(tmp_path):
    from mysuite.compress.compress import CompressError

    with pytest.raises(CompressError, match="--max-size"):
        compress_file(_photo(tmp_path / "photo.png"), "mozjpeg", tools=ToolPaths(), max_kb=2)


def test_max_kb_is_refused_for_a_lossless_codec(tmp_path):
    res = runner.invoke(app, ["compress", str(_photo(tmp_path / "p.png", size=(40, 30))), "--codec", "oxipng",
                              "--max-kb", "50", "--json"])
    assert res.exit_code == 2
    assert "mozjpeg" in json.loads(res.stdout)["errors"][0]


# --------------------------------------------------------------------------- --max-size
@need("mozjpeg")
def test_max_size_shrinks_the_longest_side_and_never_enlarges(tmp_path):
    src = _photo(tmp_path / "photo.png", size=(1200, 900))
    out = compress_file(src, "mozjpeg", tools=ToolPaths(), quality=80, max_size=400)
    with Image.open(out.output_path) as im:
        assert im.size == (400, 300)
    big = compress_file(src, "mozjpeg", tools=ToolPaths(), quality=80, max_size=5000,
                        output_path=tmp_path / "big.jpg")
    with Image.open(big.output_path) as im:
        assert im.size == (1200, 900)


# --------------------------------------------------------------------------- web metadata
@pytest.mark.parametrize("codec", ["mozjpeg", "webp", "avif", "oxipng", "pngquant"])
def test_web_output_drops_gps_and_serials_keeps_copyright_and_is_srgb(tmp_path, codec):
    if not _have(codec):
        pytest.skip(f"requires the {codec} encoder")
    src = _photo(tmp_path / "photo.jpg", size=(400, 300), quality=97, exif=_private_exif(), icc_profile=_SRGB)
    out = compress_file(src, codec, tools=ToolPaths(), quality=60, allow_larger=True)
    if codec == "avif":
        from PIL import features

        if not features.check("avif"):
            pytest.skip("this Pillow can't read AVIF")
    with Image.open(out.output_path) as im:
        exif = im.getexif()
        assert exif.get(0x8298).encode("latin-1").decode("utf-8") == "© 2026 Jane Example"
        assert exif.get(0x013B) == "Jane Example"
        assert not exif.get_ifd(0x8825), "GPS must be gone"
        assert 0xA431 not in exif.get_ifd(0x8769), "serial number must be gone"
        assert 0x0110 not in exif, "camera model is not kept either"
        assert _srgb_tagged(im)


# --------------------------------------------------------------------------- JSON report
@need("mozjpeg")
def test_json_items_report_bytes_before_after_and_ratio(tmp_path):
    src = _photo(tmp_path / "photo.png", size=(600, 400))
    res = runner.invoke(app, ["compress", str(src), "--codec", "mozjpeg", "--max-kb", "80", "--max-size", "500",
                              "--json"])
    doc = json.loads(res.stdout)
    assert res.exit_code == 0, doc
    item = doc["items"][0]
    assert item["bytes_before"] == src.stat().st_size
    assert item["bytes_after"] == Path(item["output"]).stat().st_size <= 80_000
    assert item["ratio"] == round(item["bytes_after"] / item["bytes_before"], 3)
    assert (item["width"], item["height"]) == (500, 333)
    assert 20 <= item["quality"] <= 95


@need("mozjpeg")
def test_save_for_web_preset_bundles_the_web_defaults(tmp_path):
    src = _photo(tmp_path / "photo.png", size=(3000, 2000))
    res = runner.invoke(app, ["compress", str(src), "--preset", "save-for-web", "--json"])
    doc = json.loads(res.stdout)
    assert res.exit_code == 0, doc
    item = doc["items"][0]
    assert item["bytes_after"] <= 500_000 and max(item["width"], item["height"]) == 2048


# --------------------------------------------------------------------------- tiny PNGs (CI: test_pipeline_through_mcp)
@pytest.mark.parametrize("codec", ["oxipng", "pngquant"])
def test_png_output_declares_srgb_with_the_small_chunk_not_a_600_byte_profile(tmp_path, codec):
    """A source with a colour profile is converted, and the PNG says sRGB with its 13-byte chunk. An untagged
    source stays untagged (untagged already means sRGB on the web, and every byte counts on a small icon)."""
    if not _have(codec):
        pytest.skip(f"requires the {codec} encoder")
    img = Image.new("RGB", (200, 150), (240, 240, 240))  # few colours: pngquant can always meet its quality range
    img.paste((221, 0, 0), (40, 30, 160, 120))
    img.save(tmp_path / "tagged.png", icc_profile=_SRGB)
    img.save(tmp_path / "plain.png")
    tagged = compress_file(tmp_path / "tagged.png", codec, tools=ToolPaths(), allow_larger=True)
    plain = compress_file(tmp_path / "plain.png", codec, tools=ToolPaths(), allow_larger=True)
    raw = tagged.output_path.read_bytes()
    assert b"sRGB" in raw and b"iCCP" not in raw
    assert b"sRGB" not in plain.output_path.read_bytes() and b"iCCP" not in plain.output_path.read_bytes()


def test_a_tiny_exported_logo_png_still_gets_smaller_with_oxipng(tmp_path):
    """CI's test_pipeline_through_mcp: an exported 32 px logo is about 300 bytes, and a 600-byte ICC profile made
    every oxipng result 'not smaller', so the pipeline's compress step handed nothing on."""
    if not (_have("oxipng") and shutil.which("rsvg-convert")):
        pytest.skip("requires oxipng and rsvg-convert")
    from tests.helpers import SIMPLE_SVG

    (tmp_path / "logo.svg").write_text(SIMPLE_SVG)
    res = runner.invoke(app, ["export", str(tmp_path / "logo.svg"), "--formats", "png", "--sizes", "32",
                              "--out", str(tmp_path / "dist"), "--json"])
    src = Path(json.loads(res.stdout)["items"][0]["output"])
    out = compress_file(src, "oxipng", tools=ToolPaths())
    assert out.status == "written" and out.bytes_after < out.bytes_before
