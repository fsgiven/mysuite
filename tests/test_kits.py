from __future__ import annotations

import json
import shutil

import pytest
from PIL import Image

from mysuite.cli import app
from mysuite.kits import kits as K
from tests.helpers import SIMPLE_SVG, runner

need = pytest.mark.skipif(shutil.which("rsvg-convert") is None, reason="needs rsvg-convert")


@pytest.fixture
def svg(tmp_path):
    p = tmp_path / "brand.svg"
    p.write_text(SIMPLE_SVG)
    return p


@pytest.fixture
def png(tmp_path):
    p = tmp_path / "mark.png"
    im = Image.new("RGBA", (300, 150), (0, 0, 0, 0))
    im.paste((221, 0, 0, 255), (20, 20, 130, 130))
    im.save(p)
    return p


def run_kit(*args):
    res = runner.invoke(app, ["kit", "make", *[str(a) for a in args], "--json"])
    return res, json.loads(res.stdout)


def files_of(doc):
    return {i["output"]: i for i in doc["items"] if i["status"] == "written"}


def test_list_names_every_kit():
    res = runner.invoke(app, ["kit", "list", "--json"])
    doc = json.loads(res.stdout)
    names = {i["kit"] for i in doc["items"]}
    assert {"favicon", "ios-app-icon", "android-icons", "social", "retina"} <= names


@need
@pytest.mark.parametrize("kit", ["favicon", "ios-app-icon", "android-icons", "social"])
def test_every_png_in_every_kit_has_exactly_the_specified_size(svg, tmp_path, kit):
    res, doc = run_kit(svg, "--kit", kit, "--out", tmp_path / "o", "--background", "#10121a")
    assert res.exit_code == 0, doc
    spec = {s.path: s for s in K.KITS[kit].files}
    seen = 0
    for out, item in files_of(doc).items():
        rel = out.split(f"/{kit}/", 1)[1]
        if rel in spec and spec[rel].kind == "png":
            with Image.open(out) as im:
                assert im.size == (spec[rel].width, spec[rel].height), rel
            seen += 1
    assert seen == sum(1 for s in K.KITS[kit].files if s.kind == "png")


@need
def test_favicon_kit_contents(svg, tmp_path):
    res, doc = run_kit(svg, "--kit", "favicon", "--out", tmp_path / "o")
    base = tmp_path / "o" / "brand" / "favicon"
    with Image.open(base / "favicon.ico") as ico:
        assert sorted(ico.info["sizes"]) == [(16, 16), (32, 32), (48, 48)]
    manifest = json.loads((base / "site.webmanifest").read_text())
    for icon in manifest["icons"]:
        assert (base / icon["src"]).exists()
    html = (base / "favicon-snippet.html").read_text()
    for ref in ("favicon.ico", "favicon-32x32.png", "apple-touch-icon.png", "site.webmanifest", "favicon.svg"):
        assert ref in html and (base / ref).exists()
    assert (base / "favicon.svg").read_text() == SIMPLE_SVG                      # the SVG is shipped untouched
    with Image.open(base / "apple-touch-icon.png") as touch:
        assert touch.mode == "RGB" and touch.getpixel((0, 0)) == (255, 255, 255)  # opaque, white behind


@need
def test_ios_icons_are_opaque_and_contents_json_matches_the_files(svg, tmp_path):
    run_kit(svg, "--kit", "ios-app-icon", "--out", tmp_path / "o", "--background", "#336699")
    folder = tmp_path / "o" / "brand" / "ios-app-icon" / "AppIcon.appiconset"
    contents = json.loads((folder / "Contents.json").read_text())
    assert len(contents["images"]) == 18
    for entry in contents["images"]:
        f = folder / entry["filename"]
        with Image.open(f) as im:
            pts = float(entry["size"].split("x")[0])
            assert im.size == (round(pts * int(entry["scale"][0])),) * 2
            assert im.mode == "RGB" and im.getpixel((0, 0)) == (0x33, 0x66, 0x99)     # no transparency, on the background


@need
def test_android_density_folders(svg, tmp_path):
    run_kit(svg, "--kit", "android-icons", "--out", tmp_path / "o")
    base = tmp_path / "o" / "brand" / "android-icons"
    for density, px in (("mdpi", 48), ("hdpi", 72), ("xhdpi", 96), ("xxhdpi", 144), ("xxxhdpi", 192)):
        with Image.open(base / f"mipmap-{density}" / "ic_launcher.png") as im:
            assert im.size == (px, px) and im.mode == "RGBA"
    assert Image.open(base / "play-store-icon-512.png").size == (512, 512)


@need
def test_social_uses_the_background_and_padding_changes_the_logo_size(svg, tmp_path):
    run_kit(svg, "--kit", "social", "--out", tmp_path / "a", "--background", "#10121a", "--padding", "10")
    run_kit(svg, "--kit", "social", "--out", tmp_path / "b", "--background", "#10121a", "--padding", "40")
    small = Image.open(tmp_path / "b" / "brand" / "social" / "open-graph-1200x630.png").convert("RGB")
    big = Image.open(tmp_path / "a" / "brand" / "social" / "open-graph-1200x630.png").convert("RGB")
    assert big.getpixel((0, 0)) == (0x10, 0x12, 0x1A)

    def ink(im):
        import numpy as np

        return int((np.asarray(im) != np.array([0x10, 0x12, 0x1A])).any(axis=2).sum())

    assert ink(big) > ink(small) * 2                                             # less padding = bigger logo


@need
def test_social_without_background_warns_and_uses_white(svg, tmp_path):
    res, doc = run_kit(svg, "--kit", "social", "--out", tmp_path / "o")
    assert any("white" in w for w in doc["warnings"])
    assert Image.open(tmp_path / "o" / "brand" / "social" / "avatar-400x400.png").convert("RGB").getpixel((0, 0)) == (255, 255, 255)


@need
def test_retina_kit(svg, tmp_path):
    res, doc = run_kit(svg, "--kit", "retina", "--size", "40", "--out", tmp_path / "o")
    for name, px in (("brand.png", 40), ("brand@2x.png", 80), ("brand@3x.png", 120)):
        assert Image.open(tmp_path / "o" / "brand" / name).size == (px, px)


def test_raster_logo_works_without_any_external_tool(png, tmp_path, monkeypatch):
    res, doc = run_kit(png, "--kit", "favicon", "--out", tmp_path / "o")
    assert res.exit_code == 0, doc
    base = tmp_path / "o" / "mark" / "favicon"
    assert Image.open(base / "favicon-32x32.png").size == (32, 32)
    assert not (base / "favicon.svg").exists()                                  # only an SVG logo ships favicon.svg
    skipped = [i for i in doc["items"] if i["output"].endswith("favicon.svg")]
    assert skipped and skipped[0]["status"] == "skipped"


def test_never_overwrites_and_dry_run_writes_nothing(png, tmp_path):
    res, doc = run_kit(png, "--kit", "android-icons", "--out", tmp_path / "o", "--dry-run")
    assert {i["status"] for i in doc["items"]} == {"planned"} and not (tmp_path / "o").exists()
    run_kit(png, "--kit", "android-icons", "--out", tmp_path / "o")
    target = tmp_path / "o" / "mark" / "android-icons" / "play-store-icon-512.png"
    first = target.read_bytes()
    res, doc = run_kit(png, "--kit", "android-icons", "--out", tmp_path / "o", "--padding", "30")
    assert {i["status"] for i in doc["items"]} == {"skipped_existing"} and target.read_bytes() == first
    res, doc = run_kit(png, "--kit", "android-icons", "--out", tmp_path / "o", "--padding", "30", "--overwrite")
    assert target.read_bytes() != first


@pytest.mark.parametrize("args,fragment", [
    (["--kit", "nope"], "unknown kit"), (["--kit", "social", "--background", "zzz"], "background"),
    (["--kit", "favicon", "--padding", "60"], "padding"), (["--kit", "retina", "--size", "0"], "retina size"),
])
def test_bad_options_are_clean_errors(png, tmp_path, args, fragment):
    res, doc = run_kit(png, *args, "--out", tmp_path / "o")
    assert res.exit_code == 1 and fragment in " ".join(doc["errors"]) + " ".join(i.get("error", "") for i in doc["items"])
    assert not (tmp_path / "o").exists()


def test_corrupt_logo_is_a_failed_item(tmp_path):
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"nope")
    res, doc = run_kit(bad, "--kit", "favicon", "--out", tmp_path / "o")
    assert res.exit_code == 1 and doc["items"][0]["status"] == "failed"


def test_sandbox_applies(png, tmp_path):
    jail = tmp_path / "jail"
    jail.mkdir()
    res = runner.invoke(app, ["--allow", str(jail), "kit", "make", str(png), "--kit", "favicon", "--out", str(jail / "o"), "--json"])
    assert res.exit_code == 3 and not (jail / "o").exists()


@need
def test_kit_as_a_pipeline_step(svg, tmp_path):
    from mysuite.pipeline import engine

    pipe = engine.parse({"inputs": ["brand.svg"], "step": [
        {"tool": "kit", "kit": "favicon", "out": "site"},
    ]}, base=tmp_path)
    (res,) = engine.run(pipe)
    assert res.status == "ok" and any(o.endswith("favicon.ico") for o in res.outputs)
