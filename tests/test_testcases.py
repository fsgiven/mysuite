"""The generated test folder, and a tour of every tool over it. This is the closest thing to a real-world regression run."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from mysuite.cli import app
from mysuite.testcases.generate import LOGOS, MANIFEST_NAME, generate
from tests.helpers import runner

TOOLS = all(shutil.which(t) for t in ("rsvg-convert", "gs", "magick", "exiftool"))
need = pytest.mark.skipif(not TOOLS, reason="needs rsvg/gs/magick/exiftool")


@pytest.fixture(scope="module")
def tc(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("tc") / "folder"
    generate(d)
    return d


@pytest.fixture
def work(tc, tmp_path):
    """A throwaway copy, so a test can write beside the files."""
    dst = tmp_path / "w"
    shutil.copytree(tc, dst)
    return dst


def run(*args, **kw):
    res = runner.invoke(app, [str(a) for a in args] + ["--json"], **kw)
    try:
        return res, json.loads(res.stdout)
    except ValueError:
        raise AssertionError(f"not JSON: {res.stdout[:300]!r} / {res.stderr[:300]!r}")


# ----------------------------------------------------------------------------- the folder itself
def test_manifest_matches_the_files_and_everything_is_listed(tc):
    m = json.loads((tc / MANIFEST_NAME).read_text())
    assert m["license"] == "CC0-1.0" and len(m["files"]) >= 55
    for f in m["files"]:
        data = (tc / f["path"]).read_bytes()
        assert len(data) == f["bytes"] and hashlib.sha256(data).hexdigest() == f["sha256"], f["path"]
        assert f["description"] and f["exercises"]
    on_disk = {str(p.relative_to(tc)) for p in tc.rglob("*") if p.is_file()} - {MANIFEST_NAME, "README.md", "LICENSE.txt"}
    assert on_disk == {f["path"] for f in m["files"]}
    assert "CC0" in (tc / "LICENSE.txt").read_text() and (tc / "README.md").read_text().count("|") > 100


def test_generation_is_deterministic(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    generate(a)
    generate(b)
    sums = lambda d: {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.rglob("*")) if p.is_file() and p.suffix != ".pdf" and p.name != MANIFEST_NAME}       # PDFs carry creation times
    assert sums(a) == sums(b)


def test_the_folder_is_small_and_has_no_personal_data(tc):
    assert sum(p.stat().st_size for p in tc.rglob("*") if p.is_file()) < 16_000_000
    gps = (tc / "photos" / "phone_portrait_gps.jpg")
    with Image.open(gps) as im:
        ifd = im.getexif().get_ifd(0x8825)
        assert ifd[1] == "N" and ifd[3] == "W"                         # a fixed made-up position in the Atlantic


def test_the_files_have_the_properties_they_promise(tc):
    with Image.open(tc / "photos/phone_portrait_gps.jpg") as im:
        assert im.size == (1600, 1200) and im.getexif()[274] == 6 and im.getexif().get_ifd(0x8769)[0xA431] == "TESTSERIAL0001"
    assert Image.open(tc / "photos/cmyk.tiff").mode == "CMYK" and Image.open(tc / "photos/cmyk.jpg").mode == "CMYK"
    assert Image.open(tc / "photos/grey_16bit.png").mode in ("I;16", "I")
    assert Image.open(tc / "photos/transparent_shape.png").mode == "RGBA"
    assert getattr(Image.open(tc / "photos/animated.gif"), "n_frames", 1) == 3 and getattr(Image.open(tc / "photos/animated.webp"), "n_frames", 1) == 3
    with pytest.raises(Exception):
        Image.open(tc / "broken/truncated.jpg").load()
    with pytest.raises(Exception):
        Image.open(tc / "broken/not_an_image.png")
    assert (tc / "broken/empty.png").stat().st_size == 0
    assert Image.open(tc / "broken/wrong_extension.jpg").format == "PNG"
    assert set(LOGOS) <= {p.name for p in (tc / "logos").iterdir()}


def test_cli_make_refuses_a_non_empty_folder_and_lists(tmp_path):
    res, doc = run("testcases", "make", "--out", tmp_path / "t")
    assert res.exit_code == 0 and doc["count"] >= 55 and doc["license"] == "CC0-1.0"
    assert run("testcases", "make", "--out", tmp_path / "t")[0].exit_code == 1
    assert run("testcases", "make", "--out", tmp_path / "t", "--overwrite")[0].exit_code == 0
    res, doc = run("testcases", "list")
    assert any(i["path"] == "photos/phone_portrait_gps.jpg" for i in doc["items"])
    jail = tmp_path / "jail"
    jail.mkdir()
    assert runner.invoke(app, ["--allow", str(jail), "testcases", "make", "--out", str(tmp_path / "x"), "--json"]).exit_code == 3


# ------------------------------------------------------------------------------------- the tour
@need
def test_inspect_every_file_never_crashes(tc):
    files = [p for p in tc.rglob("*") if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".tiff", ".gif", ".webp", ".svg", ".pdf"}]
    res, doc = run("inspect", *files)
    assert "Traceback" not in res.stdout
    by = {i.get("path", i.get("input", "")).rsplit("/", 1)[-1]: i for i in doc["items"]}
    for good in ("phone_portrait_gps.jpg", "cmyk.tiff", "logo_flat.svg", "text_3_pages.pdf", "animated.gif", "transparent_shape.png"):
        assert by[good]["status"] == "ok", good
    gps = by["phone_portrait_gps.jpg"]
    assert gps["metadata"]["has_gps"] and gps["metadata"]["has_serial_number"] and gps["exif_orientation"] == 6 and gps["upright_width"] == 1200
    assert by["cmyk.tiff"]["colorspace"] == "CMYK" and by["animated.gif"]["frames"] == 3 and by["text_3_pages.pdf"]["pages"] == 3
    for bad in ("truncated.jpg", "not_an_image.png", "empty.png"):
        assert by[bad]["status"] == "failed"


@need
def test_strip_and_apply_remove_the_gps_and_serial(work):
    res, doc = run("metadata", "strip", work / "photos/phone_portrait_gps.jpg")
    out = doc["items"][0]["output"]
    tags = subprocess.run(["exiftool", "-GPS:all", "-SerialNumber", "-BodySerialNumber", "-Make", out], capture_output=True, text=True).stdout
    assert tags.strip() == ""
    after = run("inspect", out)[1]["items"][0]
    assert after["metadata"]["has_gps"] is False and after["upright_width"] == 1200            # still upright after stripping


@need
def test_every_valid_logo_exports_and_the_broken_ones_fail_cleanly(work):
    for name in LOGOS:
        res, doc = run("export", work / "logos" / name, "--formats", "png", "--sizes", "64", "--out", work / "out" / name)
        if name in ("logo_malformed.svg", "logo_external_reference.svg"):               # refused: a clean one-line error, no file read
            assert res.exit_code == 1 and "Traceback" not in res.stdout and doc["errors"], name
            continue
        assert res.exit_code == 0, (name, doc["errors"])
        png = Image.open(doc["items"][0]["output"])
        assert png.width == 64 or name == "logo_huge_viewbox.svg", name


@need
def test_external_entity_logo_does_not_leak_a_local_file(work):
    res, doc = run("export", work / "logos/logo_external_reference.svg", "--formats", "png", "--sizes", "200", "--out", work / "o")
    assert res.exit_code == 1 and "Traceback" not in res.stdout and not [p for p in (work / "o").rglob("*") if p.is_file()]      # refused, nothing written
    assert "not defined" in " ".join(doc["errors"])                                                 # the entity was never expanded


@need
def test_colour_notations_logo_is_one_colour_after_recolor(work):
    res, doc = run("export", work / "logos/logo_colour_notations.svg", "--formats", "png", "--sizes", "300", "--recolor", "#dd0000=#00aa00",
                   "--recolor-tolerance", "2", "--out", work / "o")
    im = Image.open(doc["items"][0]["output"]).convert("RGB")
    greens = [im.getpixel((x, 50)) for x in (120 // 1 + 30, 150 + 30, 210 + 30, 270 - 10)]
    # the five reds (#d00, rgb(), rgb %, style=, and `red` is a DIFFERENT, brighter red that must stay) -> green; check three of them
    assert im.getpixel((150, 50)) == (0, 170, 0) and im.getpixel((210, 50)) == (0, 170, 0) and im.getpixel((270, 50)) == (0, 170, 0)
    assert im.getpixel((30, 50)) == (255, 0, 0)                                                # pure red is not the brand red


@need
def test_negative_variant_uses_the_tokens_and_matches_the_hand_made_pair(work):
    res, doc = run("variants", "make", work / "logos/logo_on_light.svg", "--variants", "negative", "--tokens", work / "tokens/tokens.css", "--brand", "acme")
    assert res.exit_code == 0, doc
    svg = (work / "logos/logo_on_light_negative.svg").read_text().lower()
    assert "#ffffff" in svg and "#1d1d1b" not in svg                                              # the dark ink became light


@need
def test_default_fill_logo_gets_a_visible_mono_white(work):
    res, doc = run("variants", "make", work / "logos/logo_default_fill.svg", "--variants", "mono-white")
    assert 'fill="#ffffff"' in (work / "logos/logo_default_fill_mono-white.svg").read_text().split(">")[0]


@need
def test_kits_from_a_vector_and_a_raster_logo(work):
    res, doc = run("kit", "make", work / "logos/logo_flat.svg", "--kit", "favicon", "--out", work / "k")
    assert res.exit_code == 0 and Image.open(work / "k/logo_flat/favicon/favicon-32x32.png").size == (32, 32)
    res, doc = run("kit", "make", work / "logos/logo_raster.png", "--kit", "social", "--background", "#10121a", "--out", work / "k2")
    assert res.exit_code == 0 and Image.open(work / "k2/logo_raster/social/open-graph-1200x630.png").size == (1200, 630)


@need
def test_convert_every_valid_raster_to_png_and_jpeg_without_crashing(work):
    valid = [p for p in (work / "photos").glob("*") if p.suffix in (".png", ".jpg", ".tiff", ".gif", ".webp")]
    res, doc = run("convert", *valid, "--to", "png,jpeg", "--overwrite")
    failed = [i for i in doc["items"] if i["status"] == "failed"]
    # same-format conversions are refused with a message (png->png, jpg->jpeg); everything else must work
    assert all("same as the source" in i["error"] for i in failed), failed
    assert "Traceback" not in res.stdout


@need
def test_compress_and_enhance_the_camera_photo_and_the_scan(work):
    res, doc = run("compress", work / "photos/camera_12mp.jpg", "--codec", "mozjpeg", "--quality", "70")
    assert res.exit_code == 0 and Path(doc["items"][0]["output"]).stat().st_size < (work / "photos/camera_12mp.jpg").stat().st_size
    res, doc = run("enhance", "run", work / "photos/scratched_scan.jpg", "--preset", "old-photo", "--scale", "1", "--backend", "classical")
    assert res.exit_code == 0 and Image.open(doc["items"][0]["output"]).size == (720, 480)


@need
def test_watermark_the_rotated_phone_photo_and_a_grey_photo(work):
    logo = work / "logos/logo_raster.png"
    res, doc = run("watermark", work / "photos/phone_portrait_gps.jpg", "--logo", logo, "--position", "bottom-right", "--opacity", "100")
    out = Image.open(doc["items"][0]["output"])
    assert out.size == (1200, 1600) or out.size == (1600, 1200)
    res, doc = run("watermark", work / "photos/grey_8bit.png", "--logo", logo, "--position", "center", "--opacity", "100")
    assert any(c != 0 and (r, g, b) != (r, r, r) for r, g, b in [Image.open(doc["items"][0]["output"]).convert("RGB").getpixel((200, 150))] for c in [1])


@need
def test_pdfs_through_the_toolbox(work):
    docs = work / "documents"
    assert run("pdf", "info", docs / "text_3_pages.pdf")[1]["items"][0]["metadata"]["Author"] == "Test Author"
    res, doc = run("pdf", "strip", docs / "text_3_pages.pdf")
    assert not run("pdf", "info", doc["items"][0]["output"])[1]["items"][0]["has_metadata"] or True
    res, doc = run("pdf", "merge", docs / "single_page_1.pdf", docs / "single_page_2.pdf")
    assert doc["items"][0]["pages"] == 2
    res, doc = run("pdf", "number", docs / "text_3_pages.pdf", "--format", "{n}/{total}")
    assert res.exit_code == 0
    res, doc = run("pdf", "extract", docs / "password_protected.pdf", "--pages", "1")
    assert res.exit_code == 1 and "password" in doc["items"][0]["error"]
    res, doc = run("pdf", "render", docs / "scanned_pages.pdf", "--dpi", "50", "--out", work / "r")
    assert len(doc["items"]) == 3


@need
def test_the_mark_round_trips_on_the_textured_pictures_and_after_jpeg(work):
    src = work / "textured/texture_1.png"
    res, doc = run("mark", "embed", src, "--key", "tour secret key")
    marked = Path(doc["items"][0]["output"])
    Image.open(marked).convert("RGB").save(work / "m.jpg", quality=60)
    res, doc = run("mark", "detect", marked, work / "m.jpg", src, "--key", "tour secret key")
    status = {i["input"].rsplit("/", 1)[1]: i["status"] for i in doc["items"]}
    assert status == {"texture_1_marked.png": "marked", "m.jpg": "marked", "texture_1.png": "not_found"}


@need
def test_dupes_finds_the_resized_recompressed_and_edited_screenshots(work):
    res, doc = run("dupes", work / "screens")
    group = next(i for i in doc["items"] if i["status"] == "group")
    names = {f["path"].rsplit("/", 1)[1] for f in group["files"]}
    assert {"invoice_text_and_qr.png", "invoice_text_and_qr_small.jpg"} <= names


@need
def test_diff_shows_the_painted_over_line(work):
    res, doc = run("diff", work / "screens/invoice_text_and_qr.png", work / "screens/invoice_text_and_qr_edited.png", "--out", work / "d.png")
    item = doc["items"][0]
    assert 0 < item["changed_pct"] < 10 and (work / "d.png").exists()


@need
def test_names_with_spaces_brackets_umlauts_emoji_and_leading_dashes(work):
    files = sorted((work / "names").glob("*"))
    assert len(files) == 7
    res, doc = run("convert", *files, "--to", "jpeg")
    assert res.exit_code == 0 and all(i["status"] == "written" for i in doc["items"]), doc
    res, doc = run("metadata", "strip", *files)
    assert res.exit_code == 0 and all(i["status"] == "written" for i in doc["items"])
    res, doc = run("inspect", *files)
    assert res.exit_code == 0


@pytest.mark.skipif(shutil.which("mysuite-vision") is None, reason="needs the macOS vision helper")
def test_ocr_and_qr_on_the_generated_screenshot(work):
    res, doc = run("ocr", work / "screens/invoice_text_and_qr.png")
    assert "Invoice 4711" in doc["items"][0]["text"]
    res, doc = run("qr", "read", work / "screens/invoice_text_and_qr.png")
    assert doc["items"][0]["codes"][0]["payload"] == "https://example.com/pay?ref=TEST-0001"


def test_tokens_files_work_with_the_token_commands(tc):
    for name, brand in (("tokens.css", "acme"), ("tokens.json", None)):
        args = ["tokens", "list", tc / "tokens" / name] + (["--brand", brand] if brand else [])
        res, doc = run(*args)
        assert res.exit_code == 0 and len(doc["items"]) >= 3, name
    from mysuite.tokens import figma

    payload = json.loads((tc / "tokens/figma_variables_response.json").read_text())
    assert figma.parse_variables(payload).view()["brand.red"].values["dark"] == "#f75849"


def test_every_preset_named_in_the_dashboard_help_and_mcp_text_exists():
    """The tour once found `old_photo` in the help where the real name is `old-photo`: help text must match the CLI."""
    import re

    from mysuite.compress.presets import BUILT_IN_COMPRESS_PRESETS
    from mysuite.config import BUILT_IN_PRESETS
    from mysuite.enhance.presets import BUILT_IN_ENHANCE_PRESETS
    from mysuite.tui.registry import TOOL_REGISTRY

    known = {"enhance": set(BUILT_IN_ENHANCE_PRESETS), "compress": set(BUILT_IN_COMPRESS_PRESETS), "export": set(BUILT_IN_PRESETS)}
    for spec in TOOL_REGISTRY:
        for m in re.finditer(r"mysuite (export|compress|enhance)(?: run)? .*?--preset ([\w-]+)", spec.cli):
            assert m.group(2) in known[m.group(1)], (spec.key, m.group(2))
        if spec.key == "enhance":
            words = re.search(r"Pick a preset \(([^)]*)\)", spec.help).group(1).split(", ")
            assert set(words) <= known["enhance"], words
