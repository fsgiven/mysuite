"""WS-D: mark checks the key once, keeps the input format and metadata, notes 16-bit sources, and has --dry-run."""
from __future__ import annotations

import json
import shutil
import subprocess

import numpy as np
import pytest
from PIL import Image

from mysuite.cli import app
from mysuite.mark import core as M
from tests.helpers import runner
from tests.test_mark import KEY, photo

need_exiftool = pytest.mark.skipif(shutil.which("exiftool") is None, reason="needs exiftool")


def run(*args):
    res = runner.invoke(app, ["mark", *[str(a) for a in args], "--json"])
    try:
        return res, json.loads(res.stdout)
    except json.JSONDecodeError:
        return res, None


def as_jpeg(src, dst):
    Image.open(src).convert("RGB").save(dst, quality=95)
    return dst


def test_a_short_key_is_one_error_not_one_per_file(tmp_path):
    files = [photo(tmp_path / f"p{i}.png", seed=i) for i in range(3)]
    res, doc = run("embed", *files, "--key", "abc")
    assert res.exit_code == 2
    assert doc["items"] == [] and len(doc["errors"]) == 1 and "at least 4" in doc["errors"][0]
    res, doc = run("detect", *files, "--key", "abc")
    assert res.exit_code == 2 and len(doc["errors"]) == 1


@pytest.mark.parametrize("name,fmt", [("p.jpg", "JPEG"), ("p.png", "PNG"), ("p.tif", "TIFF"), ("p.webp", "WEBP")])
def test_the_input_format_is_kept(tmp_path, name, fmt):
    src = tmp_path / name
    Image.open(photo(tmp_path / "base.png")).convert("RGB").save(src, format=fmt, **({"quality": 95} if fmt != "PNG" else {}))
    res, doc = run("embed", src, "--key", KEY)
    assert res.exit_code == 0, res.output
    out = doc["items"][0]["output"]
    assert out.endswith(f"p_marked{src.suffix}")
    with Image.open(out) as im:
        assert im.format == fmt
    assert run("detect", out, "--key", KEY)[1]["items"][0]["detected"]


def test_format_can_still_be_chosen(tmp_path):
    src = as_jpeg(photo(tmp_path / "base.png"), tmp_path / "p.jpg")
    res, doc = run("embed", src, "--key", KEY, "--format", "png")
    assert res.exit_code == 0 and doc["items"][0]["output"].endswith("p_marked.png")


@need_exiftool
def test_metadata_is_kept(tmp_path):
    src = as_jpeg(photo(tmp_path / "base.png"), tmp_path / "p.jpg")
    subprocess.run(["exiftool", "-q", "-overwrite_original", "-Artist=Jane Doe", "-IPTC:By-line=Jane Doe",
                    "-XMP-dc:Rights=© Jane", "-XMP-plus:DataMining=Prohibited", "-Orientation#=1", str(src)], check=True)
    res, doc = run("embed", src, "--key", KEY)
    assert res.exit_code == 0, res.output
    t = json.loads(subprocess.run(["exiftool", "-j", "-G1", "-a", "-s", doc["items"][0]["output"]], capture_output=True, text=True).stdout)[0]
    assert t["IFD0:Artist"] == "Jane Doe" and t["IPTC:By-line"] == "Jane Doe" and t["XMP-dc:Rights"] == "© Jane"
    assert t["XMP-plus:DataMining"].startswith("Prohibited")


@need_exiftool
def test_a_rotated_phone_photo_is_not_turned_twice(tmp_path):
    base = Image.open(photo(tmp_path / "base.png", size=(448, 320))).convert("RGB")
    src = tmp_path / "p.jpg"
    exif = Image.Exif()
    exif[0x0112] = 6
    base.save(src, quality=95, exif=exif)
    res, doc = run("embed", src, "--key", KEY)
    assert res.exit_code == 0, res.output
    out = doc["items"][0]["output"]
    orient = subprocess.run(["exiftool", "-s3", "-n", "-Orientation", out], capture_output=True, text=True).stdout.strip()
    with Image.open(out) as im:
        assert im.size == (320, 448)                           # pixels upright
    assert orient in ("", "1")                                 # and not rotated again by the viewer


def test_sixteen_bit_sources_get_a_note(tmp_path):
    arr = (np.asarray(Image.open(photo(tmp_path / "base.png")).convert("L"), dtype=np.uint16) * 257)
    src = tmp_path / "grey16.png"
    Image.fromarray(arr).save(src)
    res, doc = run("embed", src, "--key", KEY)
    assert res.exit_code == 0, res.output
    assert any("16-bit" in n for n in doc["items"][0]["notes"])
    with Image.open(doc["items"][0]["output"]) as im:
        assert np.asarray(im.convert("L")).mean() < 230         # not clipped to white


def test_dry_run_writes_nothing(tmp_path):
    src = photo(tmp_path / "p.png")
    res, doc = run("embed", src, "--key", KEY, "--dry-run")
    assert res.exit_code == 0
    assert doc["items"][0]["status"] == "planned" and doc["items"][0]["output"].endswith("p_marked.png")
    assert not (tmp_path / "p_marked.png").exists()
