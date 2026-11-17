"""WS-C1 item 3: convert takes `jpg`, says "not an image" plainly, warns about truncated files and dropped frames,
and a name clash no longer aborts the batch."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from mysuite.cli import app
from mysuite.convert import _parsing
from tests.helpers import runner

need_magick = pytest.mark.skipif(shutil.which("magick") is None, reason="requires ImageMagick")


def run_json(*args):
    res = runner.invoke(app, [str(a) for a in args])
    return res.exit_code, json.loads(res.stdout)


def _photo(path: Path, **save) -> Path:
    rng = np.random.default_rng(7)
    Image.fromarray(rng.integers(0, 255, (60, 80, 3), dtype=np.uint8)).save(path, **save)
    return path


@need_magick
@pytest.mark.parametrize("alias, ext", [("jpg", ".jpg"), ("JPG", ".jpg"), ("tif", ".tiff")])
def test_target_aliases(tmp_path, alias, ext):
    src = _photo(tmp_path / "p.png")
    code, doc = run_json("convert", src, "--to", alias, "--json")
    assert code == 0, doc
    out = Path(doc["items"][0]["output"])
    assert out.suffix == ext and out.exists()
    assert doc["items"][0]["format"] in ("jpeg", "tiff")


@need_magick
@pytest.mark.parametrize("content, expected", [(b"", "empty"), (b"hello, I am text", "not an image")])
def test_not_an_image_gives_a_readable_error(tmp_path, content, expected):
    bad = tmp_path / "bad.png"
    bad.write_bytes(content)
    code, doc = run_json("convert", bad, "--to", "webp", "--json")
    assert code == 1
    error = doc["items"][0]["error"]
    assert expected in error and "bad.png" in error
    assert "magick" not in error and "command failed" not in error


@need_magick
def test_truncated_file_is_converted_with_a_warning(tmp_path):
    whole = _photo(tmp_path / "whole.jpg", quality=90).read_bytes()
    cut = tmp_path / "cut.jpg"
    cut.write_bytes(whole[: len(whole) // 2])
    code, doc = run_json("convert", cut, "--to", "png", "--json")
    assert code == 0, doc
    assert doc["items"][0]["status"] == "written"
    assert any("truncated" in w for w in doc["warnings"])


@need_magick
def test_animated_gif_to_single_image_notes_one_frame_once(tmp_path):
    frames = [Image.new("RGB", (20, 20), c) for c in ("red", "green", "blue")]
    gif = tmp_path / "anim.gif"
    frames[0].save(gif, save_all=True, append_images=frames[1:], duration=100, loop=0)
    code, doc = run_json("convert", gif, "--to", "png", "--json")
    assert code == 0, doc
    notes = [w for w in doc["warnings"] if "frame" in w]
    assert len(notes) == 1 and "3 frames" in notes[0] and "first" in notes[0]
    res = runner.invoke(app, ["convert", str(gif), "--to", "jpeg", "--overwrite"])
    assert "⚠ skipping" not in res.output and "⚠" in res.output


def test_clashing_names_get_the_source_extension(tmp_path):
    png, jpg = tmp_path / "x.png", tmp_path / "x.jpg"
    paths = _parsing.output_paths_for([png, jpg], "webp")
    assert (paths[png].name, paths[jpg].name) == ("x_png.webp", "x_jpg.webp")


@need_magick
def test_cli_batch_with_a_name_clash_writes_both(tmp_path):
    _photo(tmp_path / "x.png")
    _photo(tmp_path / "x.jpg")
    code, doc = run_json("convert", tmp_path, "--to", "webp", "--json")
    assert code == 0, doc
    assert sorted(Path(i["output"]).name for i in doc["items"]) == ["x_jpg.webp", "x_png.webp"]
