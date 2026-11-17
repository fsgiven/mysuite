from __future__ import annotations

from pathlib import Path

from mysuite.compress._parsing import (
    CODECS,
    output_path_for,
    output_paths_for,
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


def test_output_paths_for_keeps_distinct_outputs(tmp_path):
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    assert output_paths_for([a, b], "webp") == {a: output_path_for(a, "webp"), b: output_path_for(b, "webp")}


def test_output_paths_for_renames_a_clash_instead_of_raising(tmp_path):
    png = tmp_path / "photo.png"
    jpg = tmp_path / "photo.jpg"
    paths = output_paths_for([png, jpg], "webp")
    assert paths[png] != paths[jpg]
