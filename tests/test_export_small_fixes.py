"""WS-A item 4: --recolor-tolerance beats the config, skips said once, near-black and SWOP CMYK hints."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from mysuite.color.cmyk import find_default_profile
from tests.helpers import cli, write_svg

need = pytest.mark.skipif(not all(shutil.which(t) for t in ("rsvg-convert", "magick", "gs")), reason="needs rsvg/magick/gs")


def _logo(tmp_path: Path, colour: str = "#dc0000") -> Path:
    return write_svg(tmp_path / "logo.svg", f'<rect width="200" height="100" fill="{colour}"/>')


@need
def test_recolor_tolerance_on_the_command_line_beats_the_config(tmp_path):
    cfg = tmp_path / "mysuite.toml"
    cfg.write_text("[export]\nrecolor_tolerance = 0.0\n")
    res = cli("export", _logo(tmp_path), "--config", cfg, "--formats", "svg", "--recolor", "#dd0000=#0000ff",
              "--recolor-tolerance", "2", "--out", tmp_path / "o", "-q")
    assert res.exit_code == 0, res.stdout
    assert "#0000ff" in next((tmp_path / "o").rglob("*.svg")).read_text()      # #dc0000 is within 2 of #dd0000


@need
def test_config_tolerance_still_applies_without_the_flag(tmp_path):
    cfg = tmp_path / "mysuite.toml"
    cfg.write_text("[export]\nrecolor_tolerance = 0.0\n")
    res = cli("export", _logo(tmp_path), "--config", cfg, "--formats", "svg", "--recolor", "#dd0000=#0000ff",
              "--out", tmp_path / "o", "-q")
    assert res.exit_code == 0, res.stdout
    assert "#0000ff" not in next((tmp_path / "o").rglob("*.svg")).read_text()


def test_a_skip_is_said_once_for_all_variants(tmp_path):
    res = cli("export", _logo(tmp_path), "--formats", "png,svg", "--profiles", "rgb,cmyk", "--sizes", "16,32",
              "--variants", "default,invert,grayscale", "--out", tmp_path / "o", "--dry-run")
    assert res.exit_code == 0, res.stdout
    assert res.stdout.count("png/cmyk") == 1 and res.stdout.count("svg/cmyk") == 1, res.stdout
    doc = cli("export", _logo(tmp_path), "--formats", "png,svg", "--profiles", "rgb,cmyk", "--sizes", "16,32",
              "--variants", "default,invert,grayscale", "--out", tmp_path / "o", "--dry-run", "--json")
    warnings = json.loads(doc.stdout)["warnings"]
    assert len([w for w in warnings if "cmyk skipped" in w]) == 2


def _cmyk_warnings(tmp_path: Path, colour: str, *extra: str) -> list[str]:
    res = cli("export", _logo(tmp_path, colour), "--formats", "pdf", "--profiles", "cmyk", "--out", tmp_path / "o",
              "--overwrite", "--json", *extra)
    assert res.exit_code == 0, res.stdout
    return json.loads(res.stdout)["warnings"]


@need
def test_near_black_rich_mix_suggests_cmyk_map(tmp_path):
    hints = [w for w in _cmyk_warnings(tmp_path, "#1d1d1b") if "--cmyk-map" in w]
    assert len(hints) == 1 and "#1d1d1b" in hints[0]


@need
def test_no_near_black_hint_when_it_is_mapped_or_pure_black_or_not_dark(tmp_path):
    assert not [w for w in _cmyk_warnings(tmp_path, "#1d1d1b", "--cmyk-map", "#1d1d1b=75/65/65/80") if "near-black" in w]
    assert not [w for w in _cmyk_warnings(tmp_path, "#000000") if "near-black" in w]
    assert not [w for w in _cmyk_warnings(tmp_path, "#dd0000") if "near-black" in w]


@need
def test_default_profile_fallback_is_warned_about(tmp_path):
    warnings = _cmyk_warnings(tmp_path, "#dd0000")
    assert [w for w in warnings if "--cmyk-profile" in w and "FOGRA39" in w]


@need
def test_no_profile_warning_when_one_is_chosen(tmp_path):
    profile = find_default_profile()
    if profile is None:
        pytest.skip("no CMYK profile on this machine")
    warnings = _cmyk_warnings(tmp_path, "#dd0000", "--cmyk-profile", str(profile))
    assert not [w for w in warnings if "--cmyk-profile" in w]


def test_no_cmyk_warnings_for_rgb_only(tmp_path):
    res = cli("export", _logo(tmp_path, "#1d1d1b"), "--formats", "png", "--sizes", "16", "--out", tmp_path / "o",
              "--dry-run", "--json")
    assert not [w for w in json.loads(res.stdout)["warnings"] if "cmyk" in w.lower()]


@need
def test_cmyk_table_and_notes_are_printed_once_for_all_variants(tmp_path):
    svg = write_svg(tmp_path / "logo.svg", '<rect width="200" height="100" fill="#dd0000"/>'
                    '<rect width="50" height="50" fill="#1d1d1b"/>')
    res = cli("export", svg, "--formats", "pdf", "--profiles", "cmyk", "--variants", "default,invert,grayscale",
              "--out", tmp_path / "o")
    assert res.exit_code == 0, res.stdout
    assert res.stdout.count("CMYK (exact") == 1 and res.stdout.count("#dd0000 ->") == 1, res.stdout
    assert res.stdout.count("near-black") == 1
