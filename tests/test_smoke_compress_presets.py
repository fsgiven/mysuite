from __future__ import annotations

import shutil
import subprocess

import pytest
from typer.testing import CliRunner

from mysuite.cli import app

pytestmark = pytest.mark.slow

skip_if_mozjpeg_missing = pytest.mark.skipif(
    not shutil.which("/opt/homebrew/opt/mozjpeg/bin/cjpeg"),
    reason="requires mozjpeg (brew install mozjpeg)",
)
skip_if_magick_missing = pytest.mark.skipif(
    shutil.which("magick") is None, reason="requires magick (brew install imagemagick)"
)

runner = CliRunner()


@pytest.fixture
def photo(tmp_path):
    path = tmp_path / "photo.png"
    subprocess.run(
        ["magick", "-size", "300x200", "gradient:#3388ff-#ff6633", "-attenuate", "0.3", "+noise", "Gaussian", str(path)],
        check=True,
    )
    return path


def test_list_presets_does_not_require_inputs():
    result = runner.invoke(app, ["compress", "--list-presets"])
    assert result.exit_code == 0
    assert "web-photo-balanced" in result.stdout


def test_missing_codec_and_preset_errors_cleanly(photo):
    result = runner.invoke(app, ["compress", str(photo)])
    assert result.exit_code != 0


def test_unknown_preset_errors_cleanly(photo):
    result = runner.invoke(app, ["compress", str(photo), "--preset", "not-a-real-preset"])
    assert result.exit_code != 0


@skip_if_magick_missing
@skip_if_mozjpeg_missing
def test_preset_run_writes_output(photo):
    result = runner.invoke(app, ["compress", str(photo), "--preset", "web-photo-balanced"])
    assert result.exit_code == 0
    assert photo.with_name("photo_compressed.jpg").exists()


@skip_if_magick_missing
@skip_if_mozjpeg_missing
def test_explicit_flag_overrides_preset_value(photo):
    default_result = runner.invoke(app, ["compress", str(photo), "--preset", "web-photo-balanced"])
    assert default_result.exit_code == 0
    default_size = photo.with_name("photo_compressed.jpg").stat().st_size

    override_result = runner.invoke(
        app, ["compress", str(photo), "--preset", "web-photo-balanced", "--quality", "5", "--overwrite"]
    )
    assert override_result.exit_code == 0
    override_size = photo.with_name("photo_compressed.jpg").stat().st_size

    assert override_size < default_size
