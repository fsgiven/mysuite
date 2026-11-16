"""P0-2: 16-bit sources must keep their tones (Pillow's I;16 -> RGB clips everything above 255 to white)."""
from __future__ import annotations

import io
import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageCms

from mysuite.cli import app
from mysuite.utils import imageio
from tests.helpers import runner

need_magick = pytest.mark.skipif(shutil.which("magick") is None, reason="needs magick")


def grey16(path: Path, size=(240, 160)) -> Path:
    """A left-to-right 16-bit grey ramp, 0 -> 65535 (mean ~ 127.5 once in 8 bits)."""
    ramp = np.linspace(0, 65535, size[0]).round().astype(np.uint16)
    Image.fromarray(np.tile(ramp, (size[1], 1))).save(path)
    return path


def rgb16(path: Path, size=(240, 160)) -> Path:
    """A 16-bit RGB PNG ramp (written by numpy -> raw PNG through Pillow is 8-bit only, so use magick)."""
    import subprocess
    subprocess.run(["magick", "-size", f"{size[0]}x{size[1]}", "gradient:#200000-#e0ffff", "-rotate", "-90",
                    "-depth", "16", f"PNG48:{path}"], check=True)
    return path


def mean(path_or_image) -> float:
    im = path_or_image if isinstance(path_or_image, Image.Image) else Image.open(path_or_image)
    return float(np.asarray(im.convert("L"), dtype=np.float64).mean())


# ------------------------------------------------------------------------------ the loader itself
@pytest.mark.parametrize("mode,peak", [("I;16", 65535), ("I", 65535), ("F", 1.0)])
def test_to_8bit_scales_instead_of_clipping(mode, peak):
    ramp = np.linspace(0, peak, 256, dtype=np.float64)
    arr = np.tile(ramp, (4, 1)).astype({"I;16": np.uint16, "I": np.int32, "F": np.float32}[mode])
    im = Image.fromarray(arr)
    assert im.mode == mode
    out = imageio.to_8bit(im)
    assert out.mode == "L"
    row = np.asarray(out)[0]
    assert row[0] == 0 and row[-1] == 255 and abs(int(row[128]) - 128) <= 1


def test_to_8bit_keeps_info_and_leaves_8bit_images_alone():
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    im = Image.fromarray(np.full((4, 4), 40000, np.uint16))
    im.info["icc_profile"] = profile
    assert imageio.to_8bit(im).info["icc_profile"] == profile
    rgb = Image.new("RGB", (4, 4), (1, 2, 3))
    assert imageio.to_8bit(rgb) is rgb


def test_open_image_applies_orientation_and_depth(tmp_path):
    arr = np.zeros((10, 20), np.uint16)
    arr[:, :10] = 65535                                  # left half white
    im = Image.fromarray(arr)
    exif = Image.Exif()
    exif[274] = 6                                        # rotate 90 cw on display
    path = tmp_path / "o.png"
    im.save(path, exif=exif.tobytes())
    out = imageio.open_image(path)
    assert out.mode == "L" and out.size == (10, 20)       # turned upright
    assert np.asarray(out)[0, 0] == 255 and np.asarray(out)[-1, 0] == 0


# ------------------------------------------------------------------------- every command that reads pixels
def run(*args):
    res = runner.invoke(app, [str(a) for a in args] + ["--json"])
    return res, json.loads(res.stdout)


def _output(doc):
    assert doc["ok"], doc
    return Path(doc["items"][0]["output"])


@pytest.mark.parametrize("make", [grey16, pytest.param(rgb16, marks=need_magick)], ids=["grey16", "rgb16"])
@pytest.mark.parametrize("argv,lo,hi", [
    (["print", "{src}", "--size", "240x160", "--fit", "cover"], 115, 140),
    (["transform", "{src}", "--resize", "120"], 115, 140),
    (["profile", "{src}", "--to", "srgb"], 115, 140),
    (["enhance", "run", "{src}", "--preset", "gentle", "--backend", "classical"], 60, 200),
    (["relight", "{src}", "--preset", "side"], 40, 210),
], ids=["print", "transform", "profile", "enhance", "relight"])
def test_16bit_sources_keep_their_tones(tmp_path, make, argv, lo, hi):
    src = make(tmp_path / "ramp.png")
    expected = mean(imageio.open_image(src))
    assert 115 < expected < 140                                       # sanity: the reference is mid-grey, not white
    res, doc = run(*[a.replace("{src}", str(src)) for a in argv])
    got = mean(_output(doc))
    assert lo < got < hi, f"{argv[0]}: mean {got:.1f} (source {expected:.1f}); 16-bit was clipped"


def test_thumbnail_and_sheet_and_inspect_see_the_ramp(tmp_path):
    from mysuite.config import ToolPaths
    from mysuite.inspect import inspect as insp
    src = grey16(tmp_path / "ramp.png")
    assert 115 < mean(insp.thumbnail(src, ToolPaths())) < 140
    res, doc = run("inspect", src)
    info = doc["items"][0]
    assert info["blank"] is False and info["bit_depth"] == 16
    assert len({c["hex"] if isinstance(c, dict) else c for c in info["dominant_colors"]}) > 1


def test_to_8bit_scales_a_16bit_transparent_grey_too():
    im = Image.fromarray(np.full((2, 2), 40000, np.uint16))
    im.info["transparency"] = 40000
    out = imageio.to_8bit(im)
    assert out.info["transparency"] == round(40000 / 257) == np.asarray(out)[0, 0]
