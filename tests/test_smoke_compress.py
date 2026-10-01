from __future__ import annotations

import shutil
import subprocess

import pytest

from mysuite.compress.compress import compress_file
from mysuite.config import ToolPaths

pytestmark = pytest.mark.slow

skip_if_mozjpeg_missing = pytest.mark.skipif(
    not shutil.which("/opt/homebrew/opt/mozjpeg/bin/cjpeg"),
    reason="requires mozjpeg (brew install mozjpeg)",
)
skip_if_webp_missing = pytest.mark.skipif(
    shutil.which("cwebp") is None, reason="requires webp (brew install webp)"
)
skip_if_avif_missing = pytest.mark.skipif(
    shutil.which("avifenc") is None, reason="requires libavif (brew install libavif)"
)
skip_if_oxipng_missing = pytest.mark.skipif(
    shutil.which("oxipng") is None, reason="requires oxipng (brew install oxipng)"
)
skip_if_pngquant_missing = pytest.mark.skipif(
    shutil.which("pngquant") is None, reason="requires pngquant (brew install pngquant)"
)
skip_if_gifsicle_missing = pytest.mark.skipif(
    shutil.which("gifsicle") is None, reason="requires gifsicle (brew install gifsicle)"
)


@pytest.fixture
def photo(tmp_path):
    path = tmp_path / "photo.png"
    subprocess.run(
        ["magick", "-size", "300x200", "gradient:#3388ff-#ff6633", "-attenuate", "0.3", "+noise", "Gaussian", str(path)],
        check=True,
    )
    return path


def _stdev(path):
    result = subprocess.run(
        ["magick", str(path), "-format", "%[standard-deviation]", "info:"],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout)


@skip_if_mozjpeg_missing
def test_mozjpeg_writes_smaller_valid_jpeg(photo):
    outcome = compress_file(photo, "mozjpeg", tools=ToolPaths(), quality=80)
    assert outcome.status == "written"
    assert outcome.output_path.exists()
    assert outcome.output_path.read_bytes()[:2] == b"\xff\xd8"  # JPEG magic bytes
    assert outcome.output_path.stat().st_size < photo.stat().st_size


@skip_if_mozjpeg_missing
def test_mozjpeg_quality_affects_output_size(photo):
    low = compress_file(photo, "mozjpeg", tools=ToolPaths(), quality=20, overwrite=True)
    low_size = low.output_path.stat().st_size
    high = compress_file(photo, "mozjpeg", tools=ToolPaths(), quality=95, overwrite=True)
    high_size = high.output_path.stat().st_size
    assert low_size < high_size


@skip_if_webp_missing
def test_webp_writes_valid_riff_webp(photo):
    outcome = compress_file(photo, "webp", tools=ToolPaths(), quality=80)
    assert outcome.status == "written"
    data = outcome.output_path.read_bytes()
    assert data[:4] == b"RIFF" and data[8:12] == b"WEBP"


@skip_if_avif_missing
def test_avif_writes_valid_avif(photo):
    outcome = compress_file(photo, "avif", tools=ToolPaths(), quality=60)
    assert outcome.status == "written"
    assert b"ftypavif" in outcome.output_path.read_bytes()[:32]


@skip_if_oxipng_missing
def test_oxipng_writes_valid_png(photo):
    outcome = compress_file(photo, "oxipng", tools=ToolPaths())
    assert outcome.status == "written"
    assert outcome.output_path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


@skip_if_pngquant_missing
def test_pngquant_shrinks_via_quantization(photo):
    outcome = compress_file(photo, "pngquant", tools=ToolPaths())
    assert outcome.status == "written"
    assert outcome.output_path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert outcome.output_path.stat().st_size < photo.stat().st_size


@skip_if_gifsicle_missing
def test_gifsicle_writes_valid_gif(photo):
    outcome = compress_file(photo, "gifsicle", tools=ToolPaths())
    assert outcome.status == "written"
    assert outcome.output_path.read_bytes()[:3] == b"GIF"


@skip_if_oxipng_missing
def test_sharpen_measurably_increases_edge_contrast(photo):
    plain = compress_file(photo, "oxipng", tools=ToolPaths())
    plain_stdev = _stdev(plain.output_path)

    sharpened = compress_file(
        photo, "oxipng", tools=ToolPaths(), overwrite=True, sharpen_amount=2.0
    )
    sharpened_stdev = _stdev(sharpened.output_path)

    assert sharpened_stdev > plain_stdev


@skip_if_mozjpeg_missing
def test_skips_existing_output_without_overwrite(photo):
    first = compress_file(photo, "mozjpeg", tools=ToolPaths(), quality=80)
    assert first.status == "written"
    second = compress_file(photo, "mozjpeg", tools=ToolPaths(), quality=80)
    assert second.status == "skipped_existing"


@skip_if_mozjpeg_missing
def test_overwrite_replaces_existing_output(photo):
    first = compress_file(photo, "mozjpeg", tools=ToolPaths(), quality=20)
    first_size = first.output_path.stat().st_size
    second = compress_file(photo, "mozjpeg", tools=ToolPaths(), quality=95, overwrite=True)
    assert second.status == "written"
    assert second.output_path.stat().st_size != first_size
