from __future__ import annotations

import json
import shutil
import subprocess

import pytest
from PIL import Image

from mysuite.cli import app
from tests.helpers import runner

need = pytest.mark.skipif(not all(shutil.which(t) for t in ("rsvg-convert", "gs", "magick", "exiftool")), reason="needs tools")


def inspect(*args):
    res = runner.invoke(app, ["inspect", *[str(a) for a in args], "--json"])
    return res, json.loads(res.stdout)


def mk(path, *args):
    target = str(path) if path.name == "g.png" else f"PNG32:{path}" if path.name == "a.png" else f"PNG24:{path}" if path.suffix == ".png" else str(path)
    subprocess.run(["magick", *args, target], check=True)
    return path


@need
def test_cmyk_tiff_alpha_png_and_gray(tmp_path):
    cmyk = mk(tmp_path / "c.tiff", "-size", "20x10", "xc:#dd0000", "-colorspace", "CMYK")
    alpha = mk(tmp_path / "a.png", "-size", "20x10", "xc:none", "-fill", "red", "-draw", "circle 10,5 10,1")
    gray = mk(tmp_path / "g.png", "-size", "20x10", "gradient:black-white", "-colorspace", "Gray")
    solid = mk(tmp_path / "s.png", "-size", "20x10", "xc:red")
    res, doc = inspect(cmyk, alpha, gray, solid)
    by = {i["path"].rsplit("/", 1)[1]: i for i in doc["items"]}
    assert by["c.tiff"]["colorspace"] == "CMYK" and by["c.tiff"]["width"] == 20
    assert by["a.png"]["alpha"] and by["a.png"]["alpha_used"]
    assert by["s.png"]["alpha"] is False and by["s.png"]["colorspace"] == "RGB"
    assert by["g.png"]["colorspace"] == "gray"


@need
def test_exif_orientation_gps_and_serial_are_reported(tmp_path):
    p = mk(tmp_path / "r.jpg", "-size", "200x300", "xc:#808080")
    subprocess.run(["exiftool", "-q", "-overwrite_original", "-Orientation#=6", "-GPSLatitude=52.5", "-GPSLatitudeRef=N",
                    "-GPSLongitude=13.4", "-GPSLongitudeRef=E", "-SerialNumber=SN1", "-Make=Acme", "-Model=Cam", str(p)], check=True)
    _, doc = inspect(p)
    i = doc["items"][0]
    assert (i["width"], i["height"]) == (200, 300) and (i["upright_width"], i["upright_height"]) == (300, 200)
    assert i["exif_orientation"] == 6
    assert i["metadata"]["has_gps"] and i["metadata"]["has_serial_number"] and i["metadata"]["camera"] == "Acme Cam"


@need
def test_clean_file_reports_no_gps(tmp_path):
    p = mk(tmp_path / "c.png", "-size", "20x20", "xc:red")
    assert inspect(p)[1]["items"][0]["metadata"]["has_gps"] is False


@need
def test_blank_and_blurry_and_sharp(tmp_path):
    blank = mk(tmp_path / "blank.png", "-size", "100x100", "xc:white")
    sharp = mk(tmp_path / "sharp.png", "-size", "200x200", "pattern:checkerboard")
    soft = mk(tmp_path / "soft.png", "-size", "200x200", "plasma:fractal", "-blur", "0x25")
    _, doc = inspect(blank, sharp, soft)
    by = {i["path"].rsplit("/", 1)[1]: i for i in doc["items"]}
    assert by["blank.png"]["blank"] is True
    assert by["sharp.png"]["blank"] is False and by["sharp.png"]["blurry"] is False
    assert by["soft.png"]["blurry"] is True and by["soft.png"]["sharpness"] < by["sharp.png"]["sharpness"]


@need
def test_animated_gif_and_multipage_pdf(tmp_path):
    gif = mk(tmp_path / "a.gif", "-delay", "10", "-size", "20x20", "xc:red", "xc:blue", "xc:green")
    pdf = mk(tmp_path / "m.pdf", "-size", "60x60", "xc:red", "xc:blue", "xc:green")
    _, doc = inspect(gif, pdf)
    by = {i["path"].rsplit("/", 1)[1]: i for i in doc["items"]}
    assert by["a.gif"]["frames"] == 3 and by["a.gif"]["animated"]
    assert by["m.pdf"]["pages"] == 3 and by["m.pdf"]["page_size_points"]


@need
def test_svg_facts(tmp_path):
    svg = tmp_path / "l.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100" viewBox="0 0 200 100">'
                   '<defs><linearGradient id="g"><stop stop-color="#d00"/></linearGradient></defs>'
                   '<rect fill="url(#g)" width="9" height="9"/><rect fill="rgb(0,0,255)" width="9" height="9"/>'
                   '<text font-family="Arial, sans-serif">hi</text><image href="https://x.example/a.png"/></svg>')
    i = inspect(svg)[1]["items"][0]
    assert i["viewbox"] == [0, 0, 200, 100] and i["gradients"] == 1 and i["has_text"] and "Arial" in i["fonts"]
    assert {c["hex"] for c in i["colors"]} == {"#dd0000", "#0000ff"}
    assert i["embedded_images"] == 1 and i["external_references"] is True


@need
def test_cmyk_pdf_is_recognised_as_cmyk_with_output_intent(tmp_path):
    from tests.helpers import cli, write_svg

    svg = write_svg(tmp_path / "s.svg", '<rect width="100" height="100" fill="#dd0000"/>', width=100, height=100)
    cli("export", svg, "--formats", "pdf", "--profiles", "cmyk", "--sizes", "100", "--out", tmp_path / "o", "-q")
    (pdf,) = (tmp_path / "o").rglob("*.pdf")
    i = inspect(pdf)[1]["items"][0]
    assert "CMYK" in i["colorspaces"] and i["output_intent"] is True


@need
def test_corrupt_file_is_a_failed_item_not_a_crash(tmp_path):
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"nope")
    good = mk(tmp_path / "ok.png", "-size", "5x5", "xc:red")
    res, doc = inspect(bad, good)
    assert res.exit_code == 1 and doc["ok"] is False
    assert {i["status"] for i in doc["items"]} == {"failed", "ok"}


@need
def test_thumbnail_and_contact_sheet(tmp_path):
    big = mk(tmp_path / "big.png", "-size", "1500x900", "gradient:red-blue")
    svg = tmp_path / "l.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100"><rect width="200" height="100" fill="#dd0000"/></svg>')
    _, doc = inspect(big, "--thumb", tmp_path / "t.png")
    with Image.open(tmp_path / "t.png") as t:
        assert max(t.size) == 512
    assert doc["thumbnail"].endswith("t.png")
    _, doc = inspect(big, svg, "--sheet", tmp_path / "sheet.png")
    with Image.open(tmp_path / "sheet.png") as s:
        assert s.width > 200 and s.height > 100
    res = runner.invoke(app, ["inspect", str(big), str(svg), "--thumb", str(tmp_path / "x.png"), "--json"])
    assert res.exit_code == 1                      # --thumb is for exactly one input


@need
def test_inspect_respects_the_sandbox(tmp_path):
    jail = tmp_path / "jail"
    jail.mkdir()
    out = mk(tmp_path / "outside.png", "-size", "5x5", "xc:red")
    res = runner.invoke(app, ["--allow", str(jail), "inspect", str(out), "--json"])
    assert res.exit_code == 3


@need
def test_inspect_is_read_only(tmp_path):
    p = mk(tmp_path / "x.png", "-size", "5x5", "xc:red")
    before = p.read_bytes()
    inspect(p)
    assert p.read_bytes() == before and [f.name for f in tmp_path.iterdir()] == ["x.png"]
