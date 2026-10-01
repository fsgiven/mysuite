"""Preset behaviour across export (config-backed) and its CLI. Phase 0."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests.helpers import SIMPLE_SVG, cli, identify

pytestmark = pytest.mark.slow
need_tools = pytest.mark.skipif(any(shutil.which(t) is None for t in ("rsvg-convert", "gs", "magick")), reason="needs tools")


@pytest.fixture
def logo(tmp_path):
    p = tmp_path / "logo.svg"
    p.write_text(SIMPLE_SVG)
    return p


@need_tools
def test_builtin_favicon_preset_makes_one_ico_with_three_sizes_plus_pngs(logo, tmp_path):
    res = cli("export", logo, "--preset", "favicon", "--out", tmp_path / "o", "-q")
    assert res.exit_code == 0, res.stdout
    files = sorted(p.suffix for p in (tmp_path / "o").rglob("*.*"))
    assert files.count(".ico") == 1 and files.count(".png") == 3
    (ico,) = (tmp_path / "o").rglob("*.ico")
    assert sorted(set(identify(ico, "%w ").split())) == ["16", "32", "48"]


@need_tools
@pytest.mark.skipif(shutil.which("iconutil") is None, reason="macOS iconutil")
def test_builtin_macos_icon_preset(logo, tmp_path):
    res = cli("export", logo, "--preset", "macos-icon", "--out", tmp_path / "o", "-q")
    assert res.exit_code == 0 and len(list((tmp_path / "o").rglob("*.icns"))) == 1


def test_unknown_preset_is_a_clean_error(logo, tmp_path):
    res = cli("export", logo, "--preset", "does-not-exist", "--out", tmp_path / "o")
    assert res.exit_code != 0 and "does-not-exist" in res.stdout


@need_tools
def test_user_toml_preset_and_flag_override_precedence(logo, tmp_path):
    cfg = tmp_path / "my.toml"
    cfg.write_text('[presets.tiny]\nsizes = [24]\nformats = ["png"]\nout_dir = "ignored"\n')
    res = cli("export", logo, "--preset", "tiny", "--config", cfg, "--out", tmp_path / "o", "-q")
    assert res.exit_code == 0, res.stdout
    assert [p.stem.split("_")[-1] for p in (tmp_path / "o").rglob("*.png")] == ["24"]
    # an explicit flag must win over the preset's value
    res = cli("export", logo, "--preset", "tiny", "--config", cfg, "--sizes", "48", "--out", tmp_path / "o2", "-q")
    assert [p.stem.split("_")[-1] for p in (tmp_path / "o2").rglob("*.png")] == ["48"]


def test_user_preset_overrides_builtin_of_same_name(logo, tmp_path):
    from mysuite.config import load_config

    cfg = tmp_path / "c.toml"
    cfg.write_text('[presets.favicon]\nsizes = [8]\nformats = ["png"]\n')
    assert load_config(cfg).presets["favicon"]["sizes"] == [8]


def test_preset_save_then_list_then_use_round_trip(logo, tmp_path):
    cfg = tmp_path / "mysuite.toml"
    res = cli("preset", "save", "mine", "--sizes", "20,40", "--formats", "png", "--quality", "70", "--config", cfg)
    assert res.exit_code == 0 and cfg.exists()
    assert "mine" in cli("preset", "list", "--config", cfg).stdout
    from mysuite.config import load_config
    assert load_config(cfg).presets["mine"]["sizes"] == ["20", "40"] or load_config(cfg).presets["mine"]["sizes"] == [20, 40]


def test_preset_save_with_nothing_to_save_is_an_error(tmp_path):
    assert cli("preset", "save", "empty", "--config", tmp_path / "x.toml").exit_code != 0


def test_preset_save_preserves_other_sections_and_comments(tmp_path):
    cfg = tmp_path / "mysuite.toml"
    cfg.write_text('# my comment\n[export]\nout_dir = "keepme"\n')
    cli("preset", "save", "p1", "--sizes", "16", "--config", cfg)
    text = cfg.read_text()
    assert "# my comment" in text and 'out_dir = "keepme"' in text and "p1" in text


def test_malformed_toml_is_a_clean_error(logo, tmp_path):
    cfg = tmp_path / "bad.toml"
    cfg.write_text("[presets.x\nbroken")
    res = cli("export", logo, "--config", cfg, "--out", tmp_path / "o")
    assert res.exit_code != 0 and res.exception is None or isinstance(res.exception, SystemExit)


def test_preset_with_a_typo_in_a_field_name_is_rejected_not_ignored(logo, tmp_path):
    cfg = tmp_path / "t.toml"
    cfg.write_text('[presets.t]\nsiezs = [16]\nformats = ["png"]\n')
    res = cli("export", logo, "--preset", "t", "--config", cfg, "--out", tmp_path / "o", "-q")
    assert res.exit_code != 0, "unknown preset keys must not be silently dropped"
