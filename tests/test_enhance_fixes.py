"""WS-C1 item 5: enhance keeps EXIF on JPEG output; `enhance history` is hidden but still works."""
from __future__ import annotations

from PIL import Image

from mysuite.cli import app
from mysuite.enhance.enhance import EnhanceSettings, enhance_file
from tests.helpers import runner


def test_jpeg_output_keeps_exif_but_not_the_rotation_flag(tmp_path):
    src = tmp_path / "phone.jpg"
    exif = Image.Exif()
    exif[0x0110] = "PhoneCam 12"          # Model
    exif[0x0112] = 6                      # rotate 90 CW
    exif.get_ifd(0x8769)[0x9003] = "2026:05:01 10:00:00"   # DateTimeOriginal
    Image.new("RGB", (60, 40), (90, 120, 150)).save(src, quality=92, exif=exif.tobytes())
    out = enhance_file(src, EnhanceSettings.from_dict({"scale": 1, "output_format": "jpg"}))
    with Image.open(out.output_path) as im:
        got = im.getexif()
        assert im.size == (40, 60)                          # pixels upright
        assert got.get(0x0110) == "PhoneCam 12"
        assert got.get_ifd(0x8769).get(0x9003) == "2026:05:01 10:00:00"
        assert got.get(0x0112, 1) == 1                      # no stale rotation: it would turn it twice


def test_history_is_hidden_from_help_but_still_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("MYSUITE_DATA_DIR", str(tmp_path))
    res = runner.invoke(app, ["enhance", "--help"])
    assert res.exit_code == 0 and "history" not in res.output
    assert runner.invoke(app, ["enhance", "history"]).exit_code == 0
