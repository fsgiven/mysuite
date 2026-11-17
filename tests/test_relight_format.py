"""WS-C1 item 6: relight writes JPEG/WebP too, and stays out of the main help and the dashboard (experimental)."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

from mysuite.cli import app
from mysuite.tui.registry import TOOL_REGISTRY
from tests.helpers import runner


def _photo(path, alpha=False):
    rng = np.random.default_rng(4)
    arr = rng.integers(60, 200, (80, 100, 3), dtype=np.uint8)
    img = Image.fromarray(arr)
    if alpha:
        img = img.convert("RGBA")
        a = Image.new("L", img.size, 0)
        a.paste(255, (20, 20, 80, 60))
        img.putalpha(a)
    img.save(path)
    return path


def _run(*args):
    res = runner.invoke(app, ["relight", *[str(a) for a in args], "--json"])
    return res.exit_code, json.loads(res.stdout)


def test_format_jpg_writes_a_jpeg_and_quality_changes_its_size(tmp_path):
    src = _photo(tmp_path / "p.png")
    code, doc = _run(src, "--format", "jpg", "--quality", "95")
    assert code == 0, doc
    hi = Path(doc["items"][0]["output"])
    assert hi.name == "p_relit.jpg"
    with Image.open(hi) as im:
        assert im.format == "JPEG"
    hi_size = hi.stat().st_size
    code, doc = _run(src, "--format", "jpg", "--quality", "30", "--overwrite")
    assert code == 0
    assert Path(doc["items"][0]["output"]).stat().st_size < hi_size


def test_format_webp(tmp_path):
    code, doc = _run(_photo(tmp_path / "p.png"), "--format", "webp", "--quality", "80")
    assert code == 0, doc
    with Image.open(doc["items"][0]["output"]) as im:
        assert im.format == "WEBP"


def test_jpeg_of_a_transparent_cutout_is_flattened_on_white(tmp_path):
    code, doc = _run(_photo(tmp_path / "cut.png", alpha=True), "--format", "jpg")
    assert code == 0, doc
    with Image.open(doc["items"][0]["output"]) as im:
        assert min(im.convert("RGB").getpixel((2, 2))) > 245


def test_png_stays_the_default(tmp_path):
    code, doc = _run(_photo(tmp_path / "p.png"))
    assert code == 0 and doc["items"][0]["output"].endswith("p_relit.png")


def test_relight_is_off_the_main_help_and_the_dashboard():
    res = runner.invoke(app, ["--help"])
    assert res.exit_code == 0 and "relight" not in res.output
    assert runner.invoke(app, ["relight", "--help"]).exit_code == 0          # still there, experimental
    for spec in TOOL_REGISTRY:
        assert spec.key != "relight"
        assert all("relight" not in cmd for _, cmd in spec.commands)
