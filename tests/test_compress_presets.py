from __future__ import annotations

import pytest

from mysuite.compress.presets import BUILT_IN_COMPRESS_PRESETS
from mysuite.config import Config, MysuiteConfigError, load_config


def test_built_in_compress_presets_available_without_any_config_file():
    config = Config()
    assert config.compress_presets == BUILT_IN_COMPRESS_PRESETS


def test_every_built_in_preset_names_a_valid_codec():
    from mysuite.compress._parsing import CODECS

    for name, settings in BUILT_IN_COMPRESS_PRESETS.items():
        assert settings["codec"] in CODECS, f"{name} names an unknown codec"


def test_resolve_compress_settings_with_no_preset_returns_only_overrides():
    config = Config()
    result = config.resolve_compress_settings(None, {"codec": "webp", "quality": 50, "method": None})
    assert result == {"codec": "webp", "quality": 50}


def test_resolve_compress_settings_preset_supplies_base_values():
    config = Config()
    result = config.resolve_compress_settings(
        "web-photo-balanced", {k: None for k in ("codec", "quality", "progressive", "subsample")}
    )
    assert result == BUILT_IN_COMPRESS_PRESETS["web-photo-balanced"]


def test_resolve_compress_settings_explicit_override_wins_over_preset():
    config = Config()
    result = config.resolve_compress_settings(
        "web-photo-balanced", {"codec": None, "quality": 10, "progressive": None, "subsample": None}
    )
    assert result["quality"] == 10
    assert result["codec"] == "mozjpeg"  # untouched fields still come from the preset


def test_resolve_compress_settings_unknown_preset_raises():
    config = Config()
    with pytest.raises(MysuiteConfigError):
        config.resolve_compress_settings("does-not-exist", {})


def test_load_config_merges_user_compress_presets_with_built_ins(tmp_path):
    toml_path = tmp_path / "mysuite.toml"
    toml_path.write_text(
        '[compress_presets.my-custom]\ncodec = "webp"\nquality = 40\n'
    )
    config = load_config(toml_path)
    assert "my-custom" in config.compress_presets
    assert config.compress_presets["my-custom"] == {"codec": "webp", "quality": 40}
    # built-ins are still present alongside the user's own
    assert "web-photo-balanced" in config.compress_presets


def test_load_config_user_preset_can_override_a_built_in_of_the_same_name(tmp_path):
    toml_path = tmp_path / "mysuite.toml"
    toml_path.write_text(
        '[compress_presets.lossless-archive]\ncodec = "oxipng"\neffort = 2\n'
    )
    config = load_config(toml_path)
    assert config.compress_presets["lossless-archive"] == {"codec": "oxipng", "effort": 2}
