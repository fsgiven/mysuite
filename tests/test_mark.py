from __future__ import annotations

import io
import json

import numpy as np
import pytest
from PIL import Image, ImageFilter

from mysuite.cli import app
from mysuite.mark import core as M
from tests.helpers import runner

KEY = "correct horse battery"


def photo(path, size=(448, 384), seed=3):
    """A photo-like picture: smooth colour fields plus texture and a few shapes."""
    rng = np.random.default_rng(seed)
    base = rng.normal(0, 1, (size[1] // 12, size[0] // 12, 3))
    big = np.asarray(Image.fromarray(((base - base.min()) / np.ptp(base) * 255).astype(np.uint8)).resize(size, Image.Resampling.BICUBIC), float)
    grain = rng.normal(0, 6, (size[1], size[0], 1))
    arr = np.clip(big * 0.8 + 30 + grain, 0, 255).astype(np.uint8)
    im = Image.fromarray(arr)
    im.save(path)
    return path


def jpeg(im, q):
    b = io.BytesIO()
    im.convert("RGB").save(b, "JPEG", quality=q)
    return Image.open(b).convert("RGB")


@pytest.fixture
def marked(tmp_path):
    src = photo(tmp_path / "p.png")
    r = M.embed_file(src, key=KEY)
    return src, r.path


def z(path, **kw):
    return M.detect_file(path, key=kw.pop("key", KEY), **kw)


def test_the_mark_is_there_but_invisible(marked):
    src, out = marked
    a, b = np.asarray(Image.open(src).convert("RGB"), int), np.asarray(Image.open(out).convert("RGB"), int)
    assert Image.open(out).size == Image.open(src).size
    assert 0 < np.abs(a - b).max() <= 14 and 10 * np.log10(255 ** 2 / np.mean((a - b) ** 2)) > 37
    d = z(out)
    assert d.detected and d.z > 9 and d.scale == 1.0


def test_wrong_key_wrong_id_and_unmarked_are_not_detected(marked, tmp_path):
    src, out = marked
    assert not z(out, key="another key!!").detected
    assert not z(out, ident="x").detected
    assert not z(src).detected and z(src).z < M.DETECT_Z - 2
    other = photo(tmp_path / "o.png", seed=9)
    assert not z(other).detected                                  # a different, unmarked picture


def test_id_changes_the_mark(tmp_path):
    src = photo(tmp_path / "p.png")
    out = M.embed_file(src, key=KEY, ident="launch-2026").path
    assert z(out, ident="launch-2026").detected and not z(out).detected and not z(out, ident="launch-2027").detected


@pytest.mark.parametrize("name", ["jpeg75", "jpeg40", "half", "threequarter", "up150", "crop", "blur", "bright"])
def test_the_mark_survives_ordinary_handling(marked, tmp_path, name):
    _, out = marked
    im = Image.open(out).convert("RGB")
    attacked = {
        "jpeg75": lambda: jpeg(im, 75), "jpeg40": lambda: jpeg(im, 40),
        "half": lambda: im.resize((im.width // 2, im.height // 2), Image.Resampling.LANCZOS),
        "threequarter": lambda: im.resize((im.width * 3 // 4, im.height * 3 // 4), Image.Resampling.LANCZOS),
        "up150": lambda: im.resize((im.width * 3 // 2, im.height * 3 // 2), Image.Resampling.LANCZOS),
        "crop": lambda: im.crop((60, 40, im.width - 40, im.height - 50)),
        "blur": lambda: im.filter(ImageFilter.GaussianBlur(1.0)),
        "bright": lambda: Image.fromarray(np.clip(np.asarray(im, int) + 25, 0, 255).astype(np.uint8)),
    }[name]()
    p = tmp_path / f"{name}.png"
    attacked.save(p)
    d = z(p)
    assert d.detected, (name, d)
    if name in ("half", "threequarter", "up150"):
        assert abs(d.scale - {"half": 0.5, "threequarter": 0.75, "up150": 1.5}[name]) < 0.15


def test_noise_and_flips_are_handled_but_heavy_blur_and_rotation_are_not(marked, tmp_path):
    """Be honest about the edges: noise averages out, mirroring is found; heavy blur and rotation erase the mark."""
    _, out = marked
    im = Image.open(out).convert("RGB")
    p = tmp_path / "x.png"
    noisy = np.clip(np.asarray(im, float) + np.random.default_rng(0).normal(0, 25, (im.height, im.width, 3)), 0, 255).astype(np.uint8)
    Image.fromarray(noisy).save(p)
    assert z(p).detected
    im.transpose(Image.FLIP_LEFT_RIGHT).save(p)
    d = z(p)
    assert d.detected and d.mirrored
    im.filter(ImageFilter.GaussianBlur(4.5)).save(p)
    assert not z(p).detected
    im.rotate(1.2, resample=Image.Resampling.BICUBIC).save(p)
    assert not z(p).detected                                              # without --deep


def test_deep_search_finds_a_slightly_rotated_copy(tmp_path):
    src = photo(tmp_path / "big.png", size=(640, 512))
    out = M.embed_file(src, key=KEY, strength="strong").path
    rotated = Image.open(out).convert("RGB").rotate(1.3, resample=Image.Resampling.BICUBIC)
    rotated.save(tmp_path / "r.png")
    assert not z(tmp_path / "r.png").detected
    d = z(tmp_path / "r.png", deep=True)
    assert d.detected and 0.8 <= abs(d.angle) <= 2.0, d


def test_alpha_and_icc_survive_and_transparent_corners_stay_transparent(tmp_path):
    rgb = Image.open(photo(tmp_path / "p.png")).convert("RGBA")
    rgb.putpixel((0, 0), (0, 0, 0, 0))
    src = tmp_path / "t.png"
    rgb.save(src)
    out = Image.open(M.embed_file(src, key=KEY).path)
    assert out.mode == "RGBA" and out.getpixel((0, 0))[3] == 0 and out.getpixel((100, 100))[3] == 255
    assert z(tmp_path / "t_marked.png").detected


def test_strengths_and_formats(tmp_path):
    src = photo(tmp_path / "p.png")
    psnrs = {s: M.embed_file(src, key=KEY, strength=s, overwrite=True).psnr_db for s in ("subtle", "standard", "strong")}
    assert psnrs["subtle"] > psnrs["standard"] > psnrs["strong"] > 34
    j = M.embed_file(src, key=KEY, fmt="jpeg", overwrite=True)
    assert j.path.suffix == ".jpg" and z(j.path).detected and j.notes


@pytest.mark.parametrize("kwargs,fragment", [({"key": "abc"}, "at least 4"), ({"key": KEY, "strength": "huge"}, "strength"), ({"key": KEY, "fmt": "gif"}, "format")])
def test_bad_options(tmp_path, kwargs, fragment):
    src = photo(tmp_path / "p.png")
    with pytest.raises(M.MarkError, match=fragment):
        M.embed_file(src, **kwargs)


def test_tiny_images_are_refused(tmp_path):
    src = tmp_path / "s.png"
    Image.new("RGB", (100, 100), "red").save(src)
    with pytest.raises(M.MarkError, match="too small"):
        M.embed_file(src, key=KEY)


# ---------------------------------------------------------------------------------- CLI
def run(*args, **kw):
    res = runner.invoke(app, ["mark", *[str(a) for a in args], "--json"], **kw)
    return res, json.loads(res.stdout)


def test_cli_embed_then_detect_round_trip(tmp_path):
    src = photo(tmp_path / "p.png")
    res, doc = run("embed", src, "--key", KEY)
    assert res.exit_code == 0 and doc["items"][0]["output"].endswith("p_marked.png") and doc["items"][0]["psnr_db"] > 37
    res, doc = run("detect", tmp_path / "p_marked.png", src, "--key", KEY)
    by = {i["input"].rsplit("/", 1)[1]: i for i in doc["items"]}
    assert by["p_marked.png"]["detected"] and by["p_marked.png"]["status"] == "marked"
    assert by["p.png"]["status"] == "not_found" and doc["threshold"] == M.DETECT_Z
    assert res.exit_code == 0 and run("detect", src, "--key", KEY, "--strict")[0].exit_code == 1


def test_key_from_the_environment_and_missing_key(tmp_path, monkeypatch):
    src = photo(tmp_path / "p.png")
    assert run("embed", src)[0].exit_code == 1
    monkeypatch.setenv("MYSUITE_MARK_KEY", KEY)
    assert run("embed", src)[0].exit_code == 0 and run("detect", tmp_path / "p_marked.png")[1]["items"][0]["detected"]


def test_cli_never_overwrites_and_reports_errors(tmp_path):
    src = photo(tmp_path / "p.png")
    run("embed", src, "--key", KEY)
    first = (tmp_path / "p_marked.png").read_bytes()
    assert run("embed", src, "--key", KEY, "--id", "other")[1]["items"][0]["status"] == "skipped_existing"
    assert (tmp_path / "p_marked.png").read_bytes() == first
    small = tmp_path / "s.png"
    Image.new("RGB", (60, 60)).save(small)
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"x")
    res, doc = run("embed", small, bad, "--key", KEY)
    assert res.exit_code == 1 and [i["status"] for i in doc["items"]] == ["failed", "failed"]
    assert run("detect", small, "--key", KEY)[0].exit_code == 1


def test_cli_sandbox(tmp_path):
    jail = tmp_path / "jail"
    jail.mkdir()
    src = photo(tmp_path / "p.png")
    assert runner.invoke(app, ["--allow", str(jail), "mark", "embed", str(src), "--key", KEY, "--json"]).exit_code == 3
    assert not (tmp_path / "p_marked.png").exists()


def test_mark_as_a_pipeline_step(tmp_path):
    from mysuite.pipeline import engine

    photo(tmp_path / "p.png")
    pipe = engine.parse({"inputs": ["p.png"], "step": [{"tool": "mark", "action": "embed", "key": KEY}]}, base=tmp_path)
    (res,) = engine.run(pipe)
    assert res.status == "ok" and res.outputs[0].endswith("p_marked.png")
