from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageCms

from mysuite.config import Config, MysuiteConfigError, load_config
from mysuite.enhance import history
from mysuite.enhance._parsing import (
    InvalidEnhanceInputError,
    check_no_output_collisions,
    output_path_for,
    resolve_input_files,
)
from mysuite.enhance.enhance import EnhanceError, EnhanceSettings, enhance_file
from mysuite.enhance.presets import BUILT_IN_ENHANCE_PRESETS
from mysuite.enhance.upscalers.classical import ClassicalUpscaler

NEUTRAL = dict(scale=1, denoise=0.0, sharpen=0.0, auto_white_balance=False,
               saturation=1.0, contrast=1.0, gamma=1.0)
P3 = Path("/System/Library/ColorSync/Profiles/Display P3.icc")


def _png(path: Path, size=(40, 30), color=(120, 90, 200)) -> Path:
    Image.new("RGB", size, color).save(path)
    return path


# ---- settings / presets ---------------------------------------------------

def test_every_built_in_preset_is_valid():
    for name, data in BUILT_IN_ENHANCE_PRESETS.items():
        EnhanceSettings.from_dict(data)


@pytest.mark.parametrize("bad", [
    {"scale": 0}, {"scale": 9}, {"denoise": 1.5}, {"sharpen": -0.1}, {"gamma": 0.0},
    {"output_format": "bmp"}, {"output_quality": 0}, {"saturation": 5},
])
def test_invalid_settings_are_rejected(bad):
    with pytest.raises(EnhanceError):
        EnhanceSettings.from_dict(bad)


def test_unknown_setting_name_is_an_error_not_silently_ignored():
    with pytest.raises(EnhanceError, match="typo_field"):
        EnhanceSettings.from_dict({"typo_field": 1})


def test_jpeg_is_accepted_as_jpg():
    assert EnhanceSettings.from_dict({"output_format": "jpeg"}).output_format == "jpg"


def test_preset_layering_explicit_value_wins():
    merged = Config().resolve_enhance_settings("prime", {"scale": 2, "denoise": None})
    assert merged["scale"] == 2 and merged["denoise"] == BUILT_IN_ENHANCE_PRESETS["prime"]["denoise"]


def test_unknown_preset_raises():
    with pytest.raises(MysuiteConfigError):
        Config().resolve_enhance_settings("nope", {})


def test_user_toml_presets_merge_with_and_override_built_ins(tmp_path):
    toml = tmp_path / "mysuite.toml"
    toml.write_text('[enhance_presets.mine]\nscale = 3\n[enhance_presets.gentle]\nscale = 5\n')
    config = load_config(toml)
    assert config.enhance_presets["mine"] == {"scale": 3}
    assert config.enhance_presets["gentle"] == {"scale": 5}
    assert "prime" in config.enhance_presets


# ---- parsing --------------------------------------------------------------

def test_output_is_beside_the_source_with_enhanced_suffix(tmp_path):
    assert output_path_for(tmp_path / "a" / "photo.jpg", "png") == tmp_path / "a" / "photo_enhanced.png"
    assert output_path_for(tmp_path / "photo.jpg", "jpg").name == "photo_enhanced.jpg"


def test_same_stem_different_extension_collides(tmp_path):
    a, b = _png(tmp_path / "x.png"), tmp_path / "x.jpg"
    Image.new("RGB", (4, 4)).save(b)
    with pytest.raises(InvalidEnhanceInputError):
        check_no_output_collisions([a, b], "png")


def test_same_stem_in_different_folders_does_not_collide(tmp_path):
    (tmp_path / "a").mkdir(); (tmp_path / "b").mkdir()
    check_no_output_collisions([_png(tmp_path / "a" / "p.png"), _png(tmp_path / "b" / "p.png")], "png")


def test_resolver_returns_absolute_paths_even_for_dash_names(tmp_path, monkeypatch):
    _png(tmp_path / "-overwrite_original.png")
    monkeypatch.chdir(tmp_path)
    (found,) = resolve_input_files([Path(".")])
    assert str(found).startswith("/")


def test_resolver_skips_unsupported_files_in_folders_but_rejects_them_when_named(tmp_path):
    _png(tmp_path / "ok.png")
    (tmp_path / "vector.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    assert [f.name for f in resolve_input_files([tmp_path])] == ["ok.png"]
    with pytest.raises(InvalidEnhanceInputError, match="unsupported"):
        resolve_input_files([tmp_path / "vector.svg"])


# ---- upscaler --------------------------------------------------------------

def test_classical_scale_and_sharpen_have_real_effect():
    from PIL import ImageDraw
    img = Image.new("RGB", (40, 30), (120, 140, 160))
    d = ImageDraw.Draw(img); d.line([(0, 0), (40, 30)], fill=(255, 255, 255)); d.line([(0, 30), (40, 0)], fill=(0, 0, 0))
    up = ClassicalUpscaler()
    low, high = up.upscale(img, scale=3, denoise=0.0, sharpen=0.0), up.upscale(img, scale=3, denoise=0.0, sharpen=1.0)
    assert low.size == (120, 90)
    assert np.abs(np.asarray(low, float) - np.asarray(high, float)).mean() > 0.1


# ---- pipeline ---------------------------------------------------------------

def test_writes_beside_source_and_never_touches_it(tmp_path):
    src = _png(tmp_path / "p.png")
    before = src.read_bytes()
    out = enhance_file(src, EnhanceSettings(scale=2), backend="classical")
    assert out.status == "written" and out.output_path == tmp_path / "p_enhanced.png"
    assert Image.open(out.output_path).size == (80, 60) and out.output_size == (80, 60)
    assert src.read_bytes() == before


def test_skips_existing_unless_overwrite(tmp_path):
    src = _png(tmp_path / "p.png")
    assert enhance_file(src, EnhanceSettings(), backend="classical").status == "written"
    assert enhance_file(src, EnhanceSettings(), backend="classical").status == "skipped_existing"
    assert enhance_file(src, EnhanceSettings(), backend="classical", overwrite=True).status == "written"


def test_exif_orientation_is_baked_in_and_output_has_no_metadata(tmp_path):
    stored = Image.new("RGB", (20, 40), (0, 0, 255))
    stored.putpixel((0, 0), (255, 0, 0))  # top-left of the STORED image
    exif = Image.Exif(); exif[0x0112] = 6; exif[0x010F] = "Apple"; exif[0x013B] = "Frank"
    src = tmp_path / "rot.jpg"; stored.save(src, quality=100, subsampling=0, exif=exif)

    out = enhance_file(src, EnhanceSettings.from_dict(NEUTRAL), backend="classical").output_path
    img = Image.open(out)
    assert img.size == (40, 20)  # upright: width/height swapped
    r, g, b = img.getpixel((39, 0))  # Orientation 6 = rotate 90 CW: stored top-left -> top-right
    assert r > 200 and b < 80
    assert not img.getexif() and "icc_profile" not in img.info and "exif" not in img.info
    assert b"Frank" not in out.read_bytes() and b"Apple" not in out.read_bytes()


def test_transparency_is_preserved_for_png_and_flattened_to_white_for_jpg(tmp_path):
    rgba = Image.new("RGBA", (20, 20), (255, 0, 0, 0)); rgba.paste((255, 0, 0, 255), (10, 0, 20, 20))
    src = tmp_path / "logo.png"; rgba.save(src)
    png = Image.open(enhance_file(src, EnhanceSettings.from_dict({**NEUTRAL, "scale": 2}), backend="classical").output_path)
    assert png.mode == "RGBA" and png.getpixel((2, 20))[3] == 0 and png.getpixel((35, 20))[3] == 255
    jpg_path = enhance_file(src, EnhanceSettings.from_dict({**NEUTRAL, "output_format": "jpg"}), backend="classical").output_path
    r, g, b = Image.open(jpg_path).convert("RGB").getpixel((2, 10))
    assert min(r, g, b) > 240  # transparent area became white, not black


@pytest.mark.skipif(not P3.exists(), reason="needs macOS's Display P3 profile")
def test_wide_gamut_profile_is_converted_to_srgb_not_just_dropped(tmp_path):
    src = tmp_path / "p3.png"
    Image.new("RGB", (8, 8), (200, 100, 100)).save(src, icc_profile=P3.read_bytes())
    out = Image.open(enhance_file(src, EnhanceSettings.from_dict(NEUTRAL), backend="classical").output_path)
    got = out.getpixel((4, 4))
    expected = ImageCms.profileToProfile(
        Image.new("RGB", (1, 1), (200, 100, 100)), ImageCms.getOpenProfile(str(P3)), ImageCms.createProfile("sRGB")
    ).getpixel((0, 0))
    assert got == expected and got != (200, 100, 100)
    assert "icc_profile" not in out.info


def test_face_enhance_on_classical_backend_is_reported_not_silent(tmp_path):
    out = enhance_file(_png(tmp_path / "p.png"), EnhanceSettings(face_enhance=True), backend="classical")
    assert any("face enhancement skipped" in n for n in out.notes)


def test_restore_scratches_setting_actually_removes_a_scratch(tmp_path):
    rng = np.random.default_rng(0)
    yy, xx = np.mgrid[0:120, 0:160]
    clean = np.clip(np.stack([90 + 0.3 * xx, 110 + 0.2 * yy, 150 - 0.1 * xx], -1) + rng.normal(0, 2, (120, 160, 3)), 0, 255)
    damaged = clean.copy(); damaged[10:110, 80] = 250
    src = tmp_path / "s.png"; Image.fromarray(damaged.astype("uint8")).save(src)
    off = np.asarray(Image.open(enhance_file(src, EnhanceSettings.from_dict(NEUTRAL), backend="classical").output_path), float)
    on = np.asarray(Image.open(enhance_file(src, EnhanceSettings.from_dict({**NEUTRAL, "restore_scratches": True}),
                                            backend="classical", overwrite=True).output_path), float)
    col = (slice(10, 110), 80)
    assert np.abs(off[col] - clean[col]).mean() > 50 and np.abs(on[col] - clean[col]).mean() < 8


def test_output_size_limit_and_unreadable_files_are_clean_errors(tmp_path):
    with pytest.raises(EnhanceError, match="MP"):
        enhance_file(_png(tmp_path / "big.png", size=(4000, 4000)), EnhanceSettings(scale=8), backend="classical")
    junk = tmp_path / "junk.png"; junk.write_bytes(b"not an image")
    with pytest.raises(EnhanceError, match="can't read"):
        enhance_file(junk, EnhanceSettings(), backend="classical")


def test_requesting_the_ai_backend_without_it_installed_is_a_clean_error(tmp_path):
    with pytest.raises(EnhanceError, match="not available"):
        enhance_file(_png(tmp_path / "p.png"), EnhanceSettings(), backend="realesrgan")


# ---- history (opt-in) -------------------------------------------------------

def test_history_is_empty_and_creates_nothing_until_recorded(tmp_path, monkeypatch):
    monkeypatch.setenv("MYSUITE_DATA_DIR", str(tmp_path / "data"))
    assert history.list_jobs() == []
    assert not (tmp_path / "data").exists()
    history.record(input_path="/a/p.jpg", output_path="/a/p_enhanced.png", preset="gentle",
                   backend="classical", status="done", duration_seconds=0.1)
    (row,) = history.list_jobs()
    assert row.input_path == "/a/p.jpg" and row.status == "done"
    assert history.clear() == 1 and history.list_jobs() == []
