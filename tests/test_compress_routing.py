from __future__ import annotations

from pathlib import Path

import pytest

from mysuite.compress._parsing import (
    CODECS,
    InvalidCompressInputError,
    check_no_output_collisions,
    output_path_for,
)


def test_output_path_for_uses_compressed_suffix_and_codec_extension():
    src = Path("/tmp/photo.png")
    assert output_path_for(src, "mozjpeg") == Path("/tmp/photo_compressed.jpg")
    assert output_path_for(src, "webp") == Path("/tmp/photo_compressed.webp")
    assert output_path_for(src, "avif") == Path("/tmp/photo_compressed.avif")
    assert output_path_for(src, "oxipng") == Path("/tmp/photo_compressed.png")
    assert output_path_for(src, "pngquant") == Path("/tmp/photo_compressed.png")
    assert output_path_for(src, "gifsicle") == Path("/tmp/photo_compressed.gif")


def test_output_path_for_same_folder_as_source():
    src = Path("/tmp/nested/dir/photo.png")
    out = output_path_for(src, "webp")
    assert out.parent == src.parent


def test_all_codecs_have_an_extension_mapping():
    assert set(CODECS) == {"mozjpeg", "webp", "avif", "oxipng", "pngquant", "gifsicle"}
    for ext in CODECS.values():
        assert ext.startswith(".")


def test_check_no_output_collisions_passes_for_distinct_outputs(tmp_path):
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    check_no_output_collisions([a, b], "webp")  # no raise


def test_check_no_output_collisions_raises_on_real_collision(tmp_path):
    png = tmp_path / "photo.png"
    jpg = tmp_path / "photo.jpg"
    with pytest.raises(InvalidCompressInputError):
        check_no_output_collisions([png, jpg], "webp")
