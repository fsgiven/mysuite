from __future__ import annotations

import numpy as np
import pytest
from PIL import Image
from typer.testing import CliRunner

from mysuite.cli import app

runner = CliRunner()


@pytest.fixture
def photo(tmp_path):
    arr = np.random.default_rng(0).integers(0, 255, (30, 40, 3), dtype=np.uint8)
    path = tmp_path / "photo.png"
    Image.fromarray(arr).save(path)
    return path


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    monkeypatch.setenv("MYSUITE_DATA_DIR", str(tmp_path / "data"))
    return tmp_path / "data"


def test_presets_lists_builtins_and_backend_status():
    result = runner.invoke(app, ["enhance", "presets"])
    assert result.exit_code == 0
    for name in ("prime", "gentle", "old-photo", "ai-art", "portrait"):
        assert name in result.stdout
    assert "AI backend" in result.stdout


def test_run_writes_output_beside_source_with_gentle_default(photo):
    result = runner.invoke(app, ["enhance", "run", str(photo), "--backend", "classical"])
    assert result.exit_code == 0, result.stdout
    out = photo.with_name("photo_enhanced.png")
    assert Image.open(out).size == (80, 60)  # gentle = 2x


def test_explicit_flag_overrides_preset(photo):
    result = runner.invoke(app, ["enhance", "run", str(photo), "--preset", "prime", "--scale", "1",
                                 "--backend", "classical"])
    assert result.exit_code == 0
    assert Image.open(photo.with_name("photo_enhanced.png")).size == (40, 30)


def test_dry_run_writes_nothing(photo):
    result = runner.invoke(app, ["enhance", "run", str(photo), "--dry-run"])
    assert result.exit_code == 0 and "photo_enhanced.png" in result.stdout
    assert not photo.with_name("photo_enhanced.png").exists()


def test_existing_output_is_skipped_without_overwrite(photo):
    runner.invoke(app, ["enhance", "run", str(photo), "--backend", "classical"])
    result = runner.invoke(app, ["enhance", "run", str(photo), "--backend", "classical"])
    assert "already existed" in result.stdout and "0 written" in result.stdout


@pytest.mark.parametrize("args", [["--preset", "nope"], ["--scale", "9"], ["--format", "bmp"], ["--backend", "gpu"]])
def test_bad_options_fail_cleanly(photo, args):
    assert runner.invoke(app, ["enhance", "run", str(photo), *args]).exit_code != 0


def test_unsupported_input_is_rejected(tmp_path):
    svg = tmp_path / "v.svg"
    svg.write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    assert runner.invoke(app, ["enhance", "run", str(svg)]).exit_code == 1


def test_history_is_off_by_default_and_leaves_no_file(photo, isolated_data):
    runner.invoke(app, ["enhance", "run", str(photo), "--backend", "classical"])
    assert not isolated_data.exists()
    assert "no recorded jobs" in runner.invoke(app, ["enhance", "history"]).stdout


def test_record_history_then_show_and_clear(photo, isolated_data):
    runner.invoke(app, ["enhance", "run", str(photo), "--backend", "classical", "--record-history"])
    shown = runner.invoke(app, ["enhance", "history"]).stdout
    assert "done" in shown and "photo.png" in shown
    assert "cleared 1" in runner.invoke(app, ["enhance", "history", "--clear"]).stdout
    assert "no recorded jobs" in runner.invoke(app, ["enhance", "history"]).stdout


def test_a_failing_file_exits_nonzero_but_others_still_process(tmp_path, photo):
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"junk")
    result = runner.invoke(app, ["enhance", "run", str(tmp_path), "--backend", "classical"])
    assert result.exit_code == 1
    assert photo.with_name("photo_enhanced.png").exists()
