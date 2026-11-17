"""WS-C1 item 4: watermark writes a sensible JPEG in one ImageMagick pass, takes position aliases and --size-by,
and says when an animation lost its frames."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, JpegImagePlugin

from mysuite.cli import app
from mysuite.config import ToolPaths
from mysuite.watermark import watermark as wm
from mysuite.watermark.watermark import watermark_file
from tests.helpers import runner

pytestmark = pytest.mark.skipif(not all(shutil.which(t) for t in ("magick", "rsvg-convert")),
                                reason="requires ImageMagick and rsvg-convert")


def _photo(path: Path, size=(1600, 1200), **save) -> Path:
    w, h = size
    y, x = np.mgrid[0:h, 0:w]
    rgb = np.dstack([np.full_like(x, 40.0), x * 255 / w, y * 200 / h + 40])     # never "red", so only the logo is
    rgb += np.random.default_rng(11).normal(0, 10, rgb.shape)
    Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8)).save(path, **save)
    return path


def _logo(path: Path) -> Path:
    img = Image.new("RGBA", (200, 100), (0, 0, 0, 0))
    img.paste((255, 0, 0, 255), (0, 0, 200, 100))
    img.save(path)
    return path


def _red_box(path: Path) -> tuple[int, int, int, int]:
    """Bounding box of the strongly red pixels (the stamped logo)."""
    a = np.asarray(Image.open(path).convert("RGB")).astype(int)
    mask = (a[..., 0] > 200) & (a[..., 1] < 90) & (a[..., 2] < 90)
    ys, xs = np.nonzero(mask)
    return xs.min(), ys.min(), xs.max(), ys.max()


def test_jpeg_output_stays_close_to_the_source_size_with_420_subsampling(tmp_path):
    src = _photo(tmp_path / "photo.jpg", quality=88)
    out = watermark_file(src, logo_path=_logo(tmp_path / "logo.png"), tools=ToolPaths())
    size = out.output_path.stat().st_size
    assert size < src.stat().st_size * 1.6, (size, src.stat().st_size)   # was about 4x
    with Image.open(out.output_path) as im:
        assert JpegImagePlugin.get_sampling(im) == 2                      # 4:2:0


def test_quality_option_changes_the_jpeg_size(tmp_path):
    src = _photo(tmp_path / "photo.jpg", quality=95)
    logo = _logo(tmp_path / "logo.png")
    hi = watermark_file(src, logo_path=logo, tools=ToolPaths(), quality=95).output_path.stat().st_size
    lo = watermark_file(src, logo_path=logo, tools=ToolPaths(), quality=60, overwrite=True).output_path.stat().st_size
    assert lo < hi * 0.7


def test_one_magick_pass_per_photo(tmp_path, monkeypatch):
    calls: list[list[str]] = []
    real = wm.run

    def counting(cmd, *a, **kw):
        calls.append([str(c) for c in cmd])
        return real(cmd, *a, **kw)

    monkeypatch.setattr(wm, "run", counting)
    watermark_file(_photo(tmp_path / "photo.jpg", quality=90), logo_path=_logo(tmp_path / "logo.png"),
                   tools=ToolPaths())
    magick = [c for c in calls if Path(c[0]).name == "magick"]
    assert len(magick) == 1, magick


@pytest.mark.parametrize("alias, where", [("bottom", "bottom-center"), ("top", "top-center"),
                                          ("left", "center-left"), ("right", "center-right"),
                                          ("middle", "center")])
def test_position_aliases(tmp_path, alias, where):
    src = _photo(tmp_path / "photo.png", size=(400, 300))
    logo = _logo(tmp_path / "logo.png")
    a = watermark_file(src, logo_path=logo, tools=ToolPaths(), position=alias,
                       ).output_path
    b_src = tmp_path / "b" / "photo.png"
    b_src.parent.mkdir()
    shutil.copy(src, b_src)
    b = watermark_file(b_src, logo_path=logo, tools=ToolPaths(), position=where).output_path
    assert _red_box(a) == _red_box(b)


def test_size_by_height_sizes_the_logo_against_the_photo_height(tmp_path):
    src = _photo(tmp_path / "photo.png", size=(1000, 400))
    out = watermark_file(src, logo_path=_logo(tmp_path / "logo.png"), tools=ToolPaths(), scale_pct=25,
                         size_by="height", margin_pct=0)
    x0, y0, x1, y1 = _red_box(out.output_path)
    assert abs((y1 - y0 + 1) - 100) <= 2          # 25 % of 400 px high
    assert abs((x1 - x0 + 1) - 200) <= 2          # the logo's own 2:1 aspect is kept


def test_cli_size_by_and_alias(tmp_path):
    src = _photo(tmp_path / "photo.png", size=(1000, 400))
    res = runner.invoke(app, ["watermark", str(src), "--logo", str(_logo(tmp_path / "logo.png")),
                              "--position", "bottom", "--size-by", "height", "--scale", "25", "--json"])
    doc = json.loads(res.stdout)
    assert res.exit_code == 0, doc
    x0, y0, x1, y1 = _red_box(Path(doc["items"][0]["output"]))
    assert abs((x0 + x1) / 2 - 500) <= 3 and abs((y1 - y0 + 1) - 100) <= 2


def test_animated_input_warns_that_one_frame_was_kept(tmp_path):
    frames = [Image.new("RGB", (120, 90), c) for c in ("white", "gray", "black")]
    gif = tmp_path / "anim.gif"
    frames[0].save(gif, save_all=True, append_images=frames[1:], duration=100, loop=0)
    res = runner.invoke(app, ["watermark", str(gif), "--logo", str(_logo(tmp_path / "logo.png")), "--json"])
    doc = json.loads(res.stdout)
    assert res.exit_code == 0, doc
    assert any("3 frames" in w and "first" in w for w in doc["warnings"])
