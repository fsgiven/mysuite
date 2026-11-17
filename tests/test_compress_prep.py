"""WS-C1 item 1: what compress feeds its encoders (flatten, orientation, sRGB, frames) and what it refuses to write."""
from __future__ import annotations

import io
import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageCms

from mysuite.cli import app
from mysuite.compress._parsing import output_paths_for
from mysuite.compress.compress import compress_file
from mysuite.compress.prep import load_for_web
from mysuite.config import ToolPaths
from tests.helpers import runner

P3_ICC = next((p for p in (Path("/System/Library/ColorSync/Profiles/Display P3.icc"),
                           Path("/usr/share/color/icc/colord/DisplayP3.icc")) if p.exists()), None)
need_p3 = pytest.mark.skipif(P3_ICC is None, reason="no Display P3 profile on this system")
need_mozjpeg = pytest.mark.skipif(not Path(ToolPaths().cjpeg).exists() and shutil.which("cjpeg") is None,
                                  reason="requires mozjpeg")
need_cwebp = pytest.mark.skipif(shutil.which("cwebp") is None, reason="requires cwebp")
need_gifsicle = pytest.mark.skipif(shutil.which("gifsicle") is None, reason="requires gifsicle")


def _transparent_png(path: Path) -> Path:
    """Transparent corners, an opaque red square in the middle; the hidden RGB under alpha is black."""
    img = Image.new("RGBA", (80, 80), (0, 0, 0, 0))
    img.paste((220, 20, 20, 255), (20, 20, 60, 60))
    img.save(path)
    return path


def _portrait_jpeg(path: Path) -> Path:
    """Stored 60x40 landscape with EXIF Orientation 6 (rotate 90 CW): shown upright it is 40x60 portrait.
    The top stored row is red, so upright the red band is on the right edge."""
    img = Image.new("RGB", (60, 40), (40, 90, 200))
    img.paste((230, 20, 20), (0, 0, 60, 8))
    exif = Image.Exif()
    exif[0x0112] = 6
    img.save(path, quality=95, exif=exif.tobytes())
    return path


def _p3_png(path: Path) -> Path:
    img = Image.new("RGB", (40, 40), (200, 60, 60))
    img.save(path, icc_profile=P3_ICC.read_bytes())
    return path


def _p3_in_srgb(rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    src = ImageCms.ImageCmsProfile(io.BytesIO(P3_ICC.read_bytes()))
    out = ImageCms.profileToProfile(Image.new("RGB", (1, 1), rgb), src, ImageCms.createProfile("sRGB"), outputMode="RGB")
    return out.getpixel((0, 0))


def _close(a, b, tol=6) -> bool:
    return all(abs(int(x) - int(y)) <= tol for x, y in zip(a, b))


# --------------------------------------------------------------------------- prep (Pillow only, runs everywhere)
def test_prep_flattens_transparency_on_white_not_black(tmp_path):
    prep = load_for_web(_transparent_png(tmp_path / "t.png"), flatten="white")
    assert prep.image.mode == "RGB"
    assert _close(prep.image.getpixel((2, 2)), (255, 255, 255), tol=1)
    assert _close(prep.image.getpixel((40, 40)), (220, 20, 20), tol=1)


def test_prep_flattens_on_a_chosen_background(tmp_path):
    prep = load_for_web(_transparent_png(tmp_path / "t.png"), flatten="#336699")
    assert _close(prep.image.getpixel((2, 2)), (0x33, 0x66, 0x99), tol=1)


def test_prep_keeps_alpha_when_not_flattening(tmp_path):
    prep = load_for_web(_transparent_png(tmp_path / "t.png"))
    assert prep.image.mode == "RGBA"
    assert prep.image.getpixel((2, 2))[3] == 0


def test_prep_turns_a_portrait_phone_photo_upright(tmp_path):
    prep = load_for_web(_portrait_jpeg(tmp_path / "p.jpg"))
    assert prep.image.size == (40, 60)
    right_edge = prep.image.getpixel((38, 30))
    left_edge = prep.image.getpixel((2, 30))
    assert right_edge[0] > 180 and left_edge[2] > 150


@need_p3
def test_prep_converts_display_p3_to_srgb(tmp_path):
    prep = load_for_web(_p3_png(tmp_path / "p3.png"))
    got = prep.image.getpixel((20, 20))
    assert _close(got, _p3_in_srgb((200, 60, 60)), tol=2)
    assert not _close(got, (200, 60, 60), tol=4)           # the raw P3 numbers would look dull


def test_prep_notes_that_an_animation_keeps_one_frame(tmp_path):
    frames = [Image.new("RGB", (20, 20), c) for c in ("red", "green", "blue")]
    path = tmp_path / "anim.gif"
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=100, loop=0)
    prep = load_for_web(path)
    assert any("3 frames" in n and "first" in n for n in prep.notes)


def test_prep_rejects_a_file_that_is_not_an_image(tmp_path):
    from mysuite.compress.compress import CompressError

    bad = tmp_path / "fake.png"
    bad.write_bytes(b"this is text, not a picture")
    with pytest.raises(CompressError, match="not an image"):
        load_for_web(bad)


# --------------------------------------------------------------------------- end to end through the encoders
@need_mozjpeg
def test_transparent_png_to_jpeg_has_white_corners(tmp_path):
    out = compress_file(_transparent_png(tmp_path / "t.png"), "mozjpeg", tools=ToolPaths(), quality=90,
                        allow_larger=True)
    with Image.open(out.output_path) as im:
        assert min(im.convert("RGB").getpixel((2, 2))) > 245


@need_mozjpeg
def test_portrait_photo_comes_out_upright(tmp_path):
    out = compress_file(_portrait_jpeg(tmp_path / "p.jpg"), "mozjpeg", tools=ToolPaths(), quality=80,
                        allow_larger=True)
    with Image.open(out.output_path) as im:
        assert im.size == (40, 60)
        assert im.getexif().get(0x0112, 1) == 1              # no stale rotation flag left to double-rotate


@need_p3
@need_cwebp
def test_p3_photo_to_webp_has_srgb_pixels_and_profile(tmp_path):
    out = compress_file(_p3_png(tmp_path / "p3.png"), "webp", tools=ToolPaths(), quality=100, allow_larger=True)
    with Image.open(out.output_path) as im:
        assert _close(im.convert("RGB").getpixel((20, 20)), _p3_in_srgb((200, 60, 60)), tol=6)
        desc = ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(io.BytesIO(im.info["icc_profile"])))
        assert "srgb" in desc.lower()


@need_gifsicle
def test_animated_gif_keeps_its_frames_with_gifsicle(tmp_path):
    frames = [Image.new("RGB", (40, 40), c) for c in ("red", "green", "blue")]
    path = tmp_path / "anim.gif"
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=100, loop=0)
    out = compress_file(path, "gifsicle", tools=ToolPaths(), allow_larger=True)
    with Image.open(out.output_path) as im:
        assert im.n_frames == 3


@need_mozjpeg
def test_never_writes_a_result_larger_than_the_source(tmp_path):
    src = tmp_path / "small.jpg"
    rng = np.random.default_rng(1)
    Image.fromarray(rng.integers(0, 255, (64, 64, 3), dtype=np.uint8)).save(src, quality=20)
    out = compress_file(src, "mozjpeg", tools=ToolPaths(), quality=100)
    assert out.status == "not_smaller"
    assert not out.output_path.exists()
    assert out.bytes_before == src.stat().st_size and out.bytes_after > out.bytes_before


# --------------------------------------------------------------------------- name clashes
def test_clashing_names_get_the_source_extension_instead_of_aborting(tmp_path):
    png, jpg, other = tmp_path / "photo.png", tmp_path / "photo.jpg", tmp_path / "other.png"
    paths = output_paths_for([png, jpg, other], "webp")
    assert paths[png].name == "photo_png_compressed.webp"
    assert paths[jpg].name == "photo_jpg_compressed.webp"
    assert paths[other].name == "other_compressed.webp"


@need_cwebp
def test_cli_batch_with_a_name_clash_writes_both(tmp_path):
    rng = np.random.default_rng(2)
    Image.fromarray(rng.integers(0, 255, (64, 64, 3), dtype=np.uint8)).save(tmp_path / "photo.png")
    Image.fromarray(rng.integers(0, 255, (64, 64, 3), dtype=np.uint8)).save(tmp_path / "photo.jpg", quality=95)
    res = runner.invoke(app, ["compress", str(tmp_path), "--codec", "webp", "--json"])
    doc = json.loads(res.stdout)
    assert res.exit_code == 0, doc
    outs = sorted(Path(i["output"]).name for i in doc["items"] if i.get("output"))
    assert outs == ["photo_jpg_compressed.webp", "photo_png_compressed.webp"]


@need_cwebp
def test_a_folder_run_skips_earlier_compressed_outputs(tmp_path):
    rng = np.random.default_rng(9)
    Image.fromarray(rng.integers(0, 255, (64, 64, 3), dtype=np.uint8)).save(tmp_path / "photo.png")
    Image.fromarray(rng.integers(0, 255, (64, 64, 3), dtype=np.uint8)).save(tmp_path / "old_compressed.png")
    res = runner.invoke(app, ["compress", str(tmp_path), "--codec", "webp", "--json"])
    doc = json.loads(res.stdout)
    assert [Path(i["input"]).name for i in doc["items"]] == ["photo.png"]
    named = runner.invoke(app, ["compress", str(tmp_path / "old_compressed.png"), "--codec", "webp", "--json"])
    assert [Path(i["input"]).name for i in json.loads(named.stdout)["items"]] == ["old_compressed.png"]
