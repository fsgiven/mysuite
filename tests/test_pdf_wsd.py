"""WS-D: PDF integrity, naming and speed fixes (reproductions from docs/plans/2026-11-16-tool-review-and-plan.md)."""
from __future__ import annotations

import json
import shutil

import pytest
from PIL import Image
from pypdf import PdfReader, PdfWriter

from mysuite.cli import app
from mysuite.config import ToolPaths
from mysuite.pdf import ops
from tests.helpers import runner
from tests.test_pdf import make_pdf

need_gs = pytest.mark.skipif(shutil.which("gs") is None, reason="needs gs")
need_rsvg = pytest.mark.skipif(shutil.which("rsvg-convert") is None, reason="needs rsvg-convert")


def run_pdf(*args):
    res = runner.invoke(app, ["pdf", *[str(a) for a in args], "--json"])
    return res, json.loads(res.stdout)


@pytest.fixture
def three(tmp_path):
    return make_pdf(tmp_path / "doc.pdf", [(300, 400), (400, 300), (300, 400)], title="Secret Title")


# ------------------------------------------------------------------ 1. Ghostscript: % in names, honest errors
@need_gs
def test_render_a_name_with_a_percent_sign(tmp_path):
    src = make_pdf(tmp_path / "100%_report.pdf", [(200, 200)])
    res, doc = run_pdf("render", src, "--dpi", "36")
    assert res.exit_code == 0, res.output
    out = tmp_path / "100%_report_p1.png"
    assert out.exists() and doc["items"][0]["output"] == str(out)
    assert not list(tmp_path.glob("*.tmp*"))


@need_gs
def test_compress_a_name_with_a_percent_sign(tmp_path):
    big = Image.effect_noise((900, 900), 80).convert("RGB")
    src = tmp_path / "50%d.pdf"
    big.save(src, format="PDF", resolution=72.0)
    res, doc = run_pdf("compress", src, "--level", "screen")
    assert res.exit_code == 0, res.output
    assert doc["items"][0]["status"] in ("written", "not_smaller")
    assert "Ghostscript (gs) installed" not in res.output


def test_missing_gs_is_reported_only_when_gs_is_missing(three, tmp_path):
    tools = ToolPaths(gs=str(tmp_path / "no-such-gs"))
    with pytest.raises(ops.PdfError, match="needs Ghostscript"):
        ops.render(three, None, False, tools, pages="1")


@need_gs
def test_a_gs_failure_is_not_called_missing_gs(three, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("some other file vanished")
    monkeypatch.setattr(ops, "_gs_run", boom)
    with pytest.raises(FileNotFoundError, match="vanished"):
        ops.render(three, tmp_path / "o", False, ToolPaths(), pages="1")


# ------------------------------------------------------------------ 2. owner-password-only PDFs open
def _encrypt(src, dst, user=""):
    w = PdfWriter(clone_from=str(src))
    w.encrypt(user_password=user, owner_password="owner-secret", algorithm="RC4-128")
    w.write(str(dst))
    return dst


def test_an_owner_password_only_pdf_opens(three, tmp_path):
    locked = _encrypt(three, tmp_path / "locked.pdf")
    res, doc = run_pdf("info", locked)
    assert res.exit_code == 0, res.output
    assert doc["items"][0]["pages"] == 3 and doc["items"][0]["encrypted"] == "owner password only"
    res, doc = run_pdf("extract", locked, "--pages", "2")
    assert res.exit_code == 0, res.output
    assert len(PdfReader(doc["items"][0]["output"]).pages) == 1


def test_a_pdf_that_needs_a_password_to_open_is_still_refused(three, tmp_path):
    locked = _encrypt(three, tmp_path / "locked.pdf", user="open-me")
    res, doc = run_pdf("info", locked)
    assert res.exit_code == 1 and "password" in doc["items"][0]["error"]


# ------------------------------------------------------------------ 3. the page selection is in the name
@pytest.mark.parametrize("spec,label", [
    ("1", "p1"), ("2-3", "p2-3"), ("3,1", "p3_1"), ("1,2,3", "p1-3"), ("last", "p3"), ("2-", "p2-3"), ("odd", "p1_3"),
])
def test_pages_label(spec, label):
    assert ops.pages_label(ops.parse_pages(spec, 3)) == label


def test_a_long_selection_gets_a_short_label():
    label = ops.pages_label(ops.parse_pages("odd", 400))
    assert len(label) <= 40 and label.startswith("p1-399")


def test_a_second_extract_with_other_pages_is_not_skipped(three, tmp_path):
    _, first = run_pdf("extract", three, "--pages", "1")
    res, second = run_pdf("extract", three, "--pages", "2-3")
    assert res.exit_code == 0 and second["items"][0]["status"] == "written"
    assert first["items"][0]["output"].endswith("doc_p1.pdf") and second["items"][0]["output"].endswith("doc_p2-3.pdf")
    assert len(PdfReader(second["items"][0]["output"]).pages) == 2


def test_split_ranges_are_named_by_the_pages_they_hold(three, tmp_path):
    _, doc = run_pdf("split", three, "--ranges", "last, 1-2", "--out", tmp_path / "r")
    names = [i["output"].rsplit("/", 1)[1] for i in doc["items"]]
    assert names == ["doc_p3.pdf", "doc_p1-2.pdf"]


# ------------------------------------------------------------------ 4. corner stamps stay on the page
def _red_box(pdf, tmp_path, name):
    import subprocess

    png = tmp_path / f"{name}.png"
    subprocess.run(["gs", "-q", "-dNOPAUSE", "-dBATCH", "-dSAFER", "-sDEVICE=png16m", "-r72", f"-sOutputFile={png}", str(pdf)], check=True)
    with Image.open(png) as im:
        rgb = im.convert("RGB")
        mask = Image.eval(rgb.getchannel("R"), lambda v: 255 if v > 150 else 0)
        g = Image.eval(rgb.getchannel("G"), lambda v: 255 if v < 90 else 0)
        from PIL import ImageChops
        return ImageChops.multiply(mask, g).getbbox(), rgb.size


@need_gs
@need_rsvg
@pytest.mark.parametrize("position", ["bottom-right", "top-left", "bottom-left", "top-right"])
@pytest.mark.parametrize("angle", ["0", "30"])
def test_a_corner_stamp_is_not_clipped(tmp_path, position, angle):
    white = Image.new("RGB", (600, 400), "white")
    src = tmp_path / "page.pdf"
    white.save(src, format="PDF", resolution=72.0)
    common = ["--text", "CONFIDENTIAL", "--opacity", "1", "--color", "#ff0000", "--size", "40", "--angle", angle, "--overwrite"]
    _, c = run_pdf("stamp", src, "--position", "center", *common, "-o", tmp_path / "center.pdf")
    _, k = run_pdf("stamp", src, "--position", position, *common, "-o", tmp_path / "corner.pdf")
    (cl, ct, cr, cb), _ = _red_box(c["items"][0]["output"], tmp_path, "c")
    (kl, kt, kr, kb), (w, h) = _red_box(k["items"][0]["output"], tmp_path, "k")
    assert abs((kr - kl) - (cr - cl)) <= 3 and abs((kb - kt) - (cb - ct)) <= 3      # the whole word, not "CONFIDE"
    assert kl >= 10 and kt >= 10 and kr <= w - 10 and kb <= h - 10                     # inside a margin


# ------------------------------------------------------------------ 7. from-images honours EXIF orientation
def test_from_images_turns_a_sideways_phone_photo_upright(tmp_path):
    im = Image.new("RGB", (400, 300), "white")
    exif = Image.Exif()
    exif[0x0112] = 6                                     # "rotate 90° clockwise to display"
    src = tmp_path / "phone.jpg"
    im.save(src, exif=exif)
    res, doc = run_pdf("from-images", src)
    assert res.exit_code == 0, res.output
    page = PdfReader(doc["items"][0]["output"]).pages[0]
    assert float(page.mediabox.height) > float(page.mediabox.width)        # shown upright: portrait


# ------------------------------------------------------------------ 5. SVG logos for stamp and from-images (kept as vectors)
LOGO_SVG = ('<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100" viewBox="0 0 200 100">'
            '<rect width="200" height="100" fill="#0057b8"/><circle cx="100" cy="50" r="40" fill="#ffd700"/></svg>')


def _has_raster(pdf) -> bool:
    for page in PdfReader(str(pdf)).pages:
        if list(page.images):
            return True
    return False


@need_gs
@need_rsvg
def test_stamp_takes_an_svg_logo_as_vectors(tmp_path):
    logo = tmp_path / "logo.svg"
    logo.write_text(LOGO_SVG)
    src = tmp_path / "page.pdf"
    Image.new("RGB", (600, 400), "white").save(src, format="PDF", resolution=72.0)
    res, doc = run_pdf("stamp", src, "--logo", logo, "--opacity", "1", "--scale", "50", "--position", "center")
    assert res.exit_code == 0, res.output
    out = doc["items"][0]["output"]
    page = PdfReader(out).pages[0]
    # the white source page is one image; the logo must not add another
    assert len(list(page.images)) == 1
    import subprocess

    png = tmp_path / "s.png"
    subprocess.run(["gs", "-q", "-dNOPAUSE", "-dBATCH", "-dSAFER", "-sDEVICE=png16m", "-r72", f"-sOutputFile={png}", out], check=True)
    with Image.open(png) as im:
        assert im.convert("RGB").getpixel((300, 200)) == (255, 215, 0)              # the circle, at the centre
        assert im.convert("RGB").getpixel((170, 200))[2] > 150                      # the blue box, 300 pt wide


@need_rsvg
def test_from_images_takes_an_svg_as_a_vector_page(tmp_path):
    logo = tmp_path / "logo.svg"
    logo.write_text(LOGO_SVG)
    photo = tmp_path / "photo.png"
    Image.new("RGB", (300, 300), "red").save(photo)
    res, doc = run_pdf("from-images", logo, photo, "--size", "a4")
    assert res.exit_code == 0, res.output
    reader = PdfReader(doc["items"][0]["output"])
    assert len(reader.pages) == 2
    first = reader.pages[0]
    assert round(float(first.mediabox.width)) in (595, 842) and not list(first.images)     # a4, and no raster: vectors
    assert list(reader.pages[1].images)


@need_rsvg
def test_from_images_svg_page_is_the_artwork_size_without_size(tmp_path):
    logo = tmp_path / "logo.svg"
    logo.write_text(LOGO_SVG)
    res, doc = run_pdf("from-images", logo)
    assert res.exit_code == 0, res.output
    page = PdfReader(doc["items"][0]["output"]).pages[0]
    assert (round(float(page.mediabox.width)), round(float(page.mediabox.height))) == (150, 75)   # 200x100 px at 96 px/in


# ------------------------------------------------------------------ 6. numbering is fast: no rsvg call per page
def _long_pdf(path, n):
    import io

    buf = io.BytesIO()
    Image.new("RGB", (300, 400), "white").save(buf, format="PDF", resolution=72.0)
    page = PdfReader(io.BytesIO(buf.getvalue())).pages[0]
    w = PdfWriter()
    for _ in range(n):
        w.add_page(page)
    w.write(str(path))
    return path


@need_rsvg
def test_numbering_many_pages_is_fast_one_tool_call_and_the_font_is_embedded(tmp_path, monkeypatch):
    import time

    src = _long_pdf(tmp_path / "long.pdf", 400)
    calls = []
    real = ops.run
    monkeypatch.setattr(ops, "run", lambda cmd, **k: (calls.append(cmd), real(cmd, **k))[1])
    t = time.perf_counter()
    r = ops.number_pages(src, None, False, ToolPaths(), fmt="Page {n} of {total}", position="bottom-right")
    assert time.perf_counter() - t < 10 and len(calls) == 1               # one rsvg-convert call, not one per page
    reader = PdfReader(str(r.path), strict=True)
    assert len(reader.pages) == 400
    assert "Page 7 of 400" in reader.pages[6].extract_text()
    for font in reader.pages[6]["/Resources"]["/Font"].values():          # a print preflight wants embedded fonts
        descriptor = font.get_object()["/FontDescriptor"].get_object()
        assert any(k in descriptor for k in ("/FontFile", "/FontFile2", "/FontFile3"))


@need_gs
def test_fast_numbers_land_where_asked(tmp_path):
    import subprocess

    src = _long_pdf(tmp_path / "doc.pdf", 2)
    for position in ("bottom-right", "top-left", "bottom-center"):
        out = tmp_path / f"{position}.pdf"
        ops.number_pages(src, out, True, ToolPaths(), fmt="{n}/{total}", position=position, size=20, color="#ff0000")
        png = tmp_path / f"{position}.png"
        subprocess.run(["gs", "-q", "-dNOPAUSE", "-dBATCH", "-dSAFER", "-sDEVICE=png16m", "-r72", "-dFirstPage=1", "-dLastPage=1",
                        f"-sOutputFile={png}", str(out)], check=True)
        with Image.open(png) as im:
            red = Image.eval(im.convert("RGB").getchannel("G"), lambda v: 255 if v < 100 else 0)
            l, t, r, b = red.getbbox()
        assert 20 <= l and r <= 280 and 20 <= t and b <= 380
        if position == "bottom-right":
            assert r >= 300 - 28 - 4 and b >= 400 - 28 - 4
        if position == "top-left":
            assert l <= 28 + 3 and t <= 28 + 6
        if position == "bottom-center":
            assert abs((l + r) / 2 - 150) <= 3


def test_numbering_with_non_latin_text_still_works(tmp_path):
    if shutil.which("rsvg-convert") is None:
        pytest.skip("needs rsvg-convert")
    src = _long_pdf(tmp_path / "doc.pdf", 2)
    r = ops.number_pages(src, None, False, ToolPaths(), fmt="第{n}页")
    assert len(PdfReader(str(r.path)).pages) == 2


def test_numbering_rejects_a_bad_colour(tmp_path):
    src = _long_pdf(tmp_path / "doc.pdf", 1)
    with pytest.raises(ops.PdfError, match="colour"):
        ops.number_pages(src, None, False, ToolPaths(), color="not-a-colour")


# ------------------------------------------------------------------ 8. --dry-run on every PDF command that writes
@pytest.mark.parametrize("args", [
    ["merge", "{a}", "{b}"], ["split", "{a}", "--every", "1"], ["extract", "{a}", "--pages", "1"], ["rotate", "{a}"],
    ["resize", "{a}", "--size", "a4"], ["strip", "{a}"], ["number", "{a}"], ["stamp", "{a}", "--text", "DRAFT"],
    ["images", "{a}"], ["render", "{a}"], ["from-images", "{png}"], ["compress", "{a}"],
])
def test_every_pdf_writer_has_a_dry_run_that_writes_nothing(tmp_path, args):
    a = make_pdf(tmp_path / "a.pdf", [(200, 200), (200, 300)])
    b = make_pdf(tmp_path / "b.pdf", [(200, 200)])
    png = tmp_path / "p.png"
    Image.new("RGB", (50, 50), "blue").save(png)
    before = sorted(p.name for p in tmp_path.iterdir())
    argv = [x.format(a=a, b=b, png=png) for x in args]
    res, doc = run_pdf(*argv, "--dry-run")
    assert res.exit_code == 0, res.output
    assert sorted(p.name for p in tmp_path.iterdir()) == before
    assert doc["items"] and all(i["status"] == "planned" for i in doc["items"]), doc["items"]
    assert all(i.get("output") for i in doc["items"])


# ------------------------------------------------------------------ 9. compress --max-size picks the level
@pytest.fixture
def heavy(tmp_path):
    pages = [Image.effect_noise((1400, 1000), 60).convert("RGB") for _ in range(2)]
    src = tmp_path / "heavy.pdf"
    pages[0].save(src, format="PDF", save_all=True, append_images=pages[1:], resolution=150.0)
    return src


@pytest.mark.parametrize("text,expected", [("10MB", 10_000_000), ("500 kb", 500_000), ("1.5mb", 1_500_000), ("2000", 2000)])
def test_parse_byte_size(text, expected):
    assert ops.parse_byte_size(text) == expected


@pytest.mark.parametrize("text", ["", "ten MB", "5 GB", "-1MB", "0"])
def test_bad_byte_sizes(text):
    with pytest.raises(ops.PdfError):
        ops.parse_byte_size(text)


@need_gs
def test_compress_max_size_picks_the_best_level_that_fits(heavy, tmp_path):
    screen = ops.compress(heavy, "screen", tmp_path / "screen.pdf", True, ToolPaths())
    limit = screen.extra["bytes_after"] + 1
    res, doc = run_pdf("compress", heavy, "--max-size", str(limit))
    assert res.exit_code == 0, res.output
    item = doc["items"][0]
    assert item["status"] == "written" and item["bytes_after"] <= limit and item["level"] in ops.COMPRESS_LEVELS
    assert (tmp_path / "heavy_compressed.pdf").stat().st_size <= limit


@need_gs
def test_compress_max_size_that_cannot_be_reached_writes_nothing(heavy, tmp_path):
    res, doc = run_pdf("compress", heavy, "--max-size", "1KB")
    assert res.exit_code == 1
    assert "1 KB" in doc["items"][0]["error"] and "screen" in doc["items"][0]["error"]
    assert not (tmp_path / "heavy_compressed.pdf").exists()


@need_gs
def test_compress_max_size_on_a_small_file_says_so(heavy, tmp_path):
    res, doc = run_pdf("compress", heavy, "--max-size", "500MB")
    assert res.exit_code == 0
    assert doc["items"][0]["status"] == "not_smaller" and "already" in doc["items"][0]["note"]


def test_compress_level_and_max_size_together_is_an_error(heavy):
    res, doc = run_pdf("compress", heavy, "--max-size", "1MB", "--level", "screen")
    assert res.exit_code != 0


# ------------------------------------------------------------------ 10/11. render jpg alias; an empty `pdf images` says so
@need_gs
def test_render_accepts_jpg(three, tmp_path):
    res, doc = run_pdf("render", three, "--format", "jpg", "--pages", "1", "--dpi", "30")
    assert res.exit_code == 0, res.output
    assert doc["items"][0]["output"].endswith("doc_p1.jpg")


def test_pdf_images_with_no_pictures_says_so(tmp_path):
    w = PdfWriter()
    w.add_blank_page(200, 200)
    src = tmp_path / "text_only.pdf"
    w.write(str(src))
    res, doc = run_pdf("images", src)
    assert res.exit_code == 0
    assert doc["items"][0]["status"] == "no_images" and "no embedded pictures" in doc["items"][0]["note"]
    assert "no embedded pictures" in " ".join(doc["warnings"])


# ------------------------------------------------------------------ preflight facts in `pdf info`
def test_info_preflight_on_a_picture_pdf(tmp_path):
    src = tmp_path / "pic.pdf"
    Image.new("RGB", (600, 300), "red").save(src, format="PDF", resolution=150.0)        # 4x2 in page, 150 ppi
    res, doc = run_pdf("info", src)
    pre = doc["items"][0]["preflight"]
    assert pre["colour_spaces"] == ["DeviceRGB"] and pre["images"] == 1
    assert pre["lowest_image_ppi"] == 150
    assert pre["fonts"] == [] and pre["fonts_not_embedded"] == []
    assert pre["trim_box"] is False and pre["bleed_box"] is False and pre["standard"] is None


@need_rsvg
def test_info_preflight_sees_embedded_and_missing_fonts_and_boxes(tmp_path):
    from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, FloatObject, NameObject

    src = _long_pdf(tmp_path / "doc.pdf", 1)
    numbered = ops.number_pages(src, None, False, ToolPaths()).path            # embedded subset font
    w = PdfWriter(clone_from=str(numbered))
    page = w.pages[0]
    core = w._add_object(DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                                           NameObject("/BaseFont"): NameObject("/Times-Roman")}))
    page["/Resources"]["/Font"][NameObject("/FX")] = core
    page[NameObject("/TrimBox")] = ArrayObject([FloatObject(9), FloatObject(9), FloatObject(291), FloatObject(391)])
    page[NameObject("/BleedBox")] = ArrayObject([FloatObject(0), FloatObject(0), FloatObject(300), FloatObject(400)])
    out = tmp_path / "pre.pdf"
    w.write(str(out))
    _, doc = run_pdf("info", out)
    pre = doc["items"][0]["preflight"]
    assert "Times-Roman" in pre["fonts_not_embedded"] and any("Helvetica" in f for f in pre["fonts"])
    assert not any("Helvetica" in f for f in pre["fonts_not_embedded"])
    assert pre["trim_box"] is True and pre["bleed_box"] is True


# ------------------------------------------------------------------ review follow-ups
@need_gs
@need_rsvg
def test_a_stamp_lands_right_on_a_page_whose_box_does_not_start_at_zero(tmp_path):
    from pypdf.generic import ArrayObject, FloatObject, NameObject

    src = tmp_path / "page.pdf"
    Image.new("RGB", (600, 400), "white").save(src, format="PDF", resolution=72.0)
    w = PdfWriter(clone_from=str(src))
    w.pages[0][NameObject("/MediaBox")] = ArrayObject([FloatObject(100), FloatObject(100), FloatObject(600), FloatObject(400)])
    shifted = tmp_path / "shifted.pdf"
    w.write(str(shifted))
    _, doc = run_pdf("stamp", shifted, "--text", "CONFIDENTIAL", "--opacity", "1", "--color", "#ff0000", "--position", "top-right", "--size", "30")
    (l, t, r, b), (pw, ph) = _red_box(doc["items"][0]["output"], tmp_path, "s")
    assert pw - 24 - 12 <= r <= pw - 10 and 10 <= t <= 24 + 12    # in the top-right corner of the visible 500x300 page


def test_from_images_keeps_png_pages_sharp(tmp_path):
    import io as _io

    im = Image.new("RGB", (200, 200), "white")
    for x in range(0, 200, 2):
        im.paste((0, 0, 0), (x, 0, x + 1, 200))          # 1 px lines: JPEG q75 4:2:0 smears them
    src = tmp_path / "lines.png"
    im.save(src)
    _, doc = run_pdf("from-images", src)
    xobj = list(PdfReader(doc["items"][0]["output"]).pages[0]["/Resources"]["/XObject"].values())[0].get_object()
    assert xobj["/Filter"] != "/DCTDecode"                # lossless, not a q75 JPEG
    data = list(PdfReader(doc["items"][0]["output"]).pages[0].images)[0].image.convert("RGB")
    diff = sum(abs(a - b) for a, b in zip(data.tobytes(), im.tobytes())) / (200 * 200 * 3)
    assert diff < 1.0


def test_pdf_strip_removes_xmp_on_images_and_annotation_dates(tmp_path):
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, TextStringObject

    src = tmp_path / "doc.pdf"
    Image.new("RGB", (100, 100), "white").save(src, format="PDF")
    w = PdfWriter(clone_from=str(src))
    img = list(w.pages[0]["/Resources"]["/XObject"].values())[0].get_object()
    xmp = DecodedStreamObject()
    xmp.set_data(b"<x:xmpmeta>Frank Photoshop secret-camera</x:xmpmeta>")
    img[NameObject("/Metadata")] = w._add_object(xmp)
    note = DictionaryObject({NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Text"),
                             NameObject("/T"): TextStringObject("Frank"), NameObject("/M"): TextStringObject("D:20260101"),
                             NameObject("/CreationDate"): TextStringObject("D:20260101")})
    w.pages[0][NameObject("/Annots")] = w._add_object(__import__("pypdf").generic.ArrayObject([w._add_object(note)]))
    dirty = tmp_path / "dirty.pdf"
    w.write(str(dirty))
    _, doc = run_pdf("strip", dirty)
    raw = open(doc["items"][0]["output"], "rb").read()
    assert b"secret-camera" not in raw and b"Frank" not in raw and b"D:20260101" not in raw


def test_preflight_survives_an_iccbased_space_without_n(tmp_path):
    assert ops._cs_name(["/ICCBased", {}]) .startswith("ICCBased")
