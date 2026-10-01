from __future__ import annotations

import json
import shutil
import subprocess

import pytest
from PIL import Image
from pypdf import PdfReader

from mysuite.cli import app
from mysuite.pdf import ops
from tests.helpers import runner

need_gs = pytest.mark.skipif(shutil.which("gs") is None, reason="needs gs")
need_rsvg = pytest.mark.skipif(shutil.which("rsvg-convert") is None, reason="needs rsvg-convert")


def make_pdf(path, sizes, title=None):
    """A real PDF with one page per (w, h) in points, drawn with Pillow."""
    pages = []
    for i, (w, h) in enumerate(sizes):
        im = Image.new("RGB", (w, h), "white")
        im.paste((255 * (i % 2), 80 * i % 255, 200), (w // 8, h // 8, w * 7 // 8, h * 7 // 8))
        pages.append(im)
    pages[0].save(path, format="PDF", save_all=True, append_images=pages[1:], resolution=72.0, title=title or "")
    return path


def run_pdf(*args):
    res = runner.invoke(app, ["pdf", *[str(a) for a in args], "--json"])
    return res, json.loads(res.stdout)


def sizes_of(path):
    return [(round(float(p.mediabox.width)), round(float(p.mediabox.height))) for p in PdfReader(str(path)).pages]


@pytest.fixture
def three(tmp_path):
    return make_pdf(tmp_path / "doc.pdf", [(300, 400), (400, 300), (300, 400)], title="Secret Title")


# ----------------------------------------------------------------------- page ranges
@pytest.mark.parametrize("spec,count,expected", [
    ("1", 5, [0]), ("2-3", 5, [1, 2]), ("1,3-4", 5, [0, 2, 3]), ("4-", 5, [3, 4]), ("last", 5, [4]), ("odd", 5, [0, 2, 4]),
    ("even", 5, [1, 3]), ("all", 3, [0, 1, 2]), ("-3", 5, [0, 1, 2]), ("3,1", 5, [2, 0]),
])
def test_page_ranges(spec, count, expected):
    assert ops.parse_pages(spec, count) == expected


@pytest.mark.parametrize("spec", ["0", "6", "3-2", "a", "1,,2", "2-9", "--"])
def test_bad_page_ranges(spec):
    with pytest.raises(ops.PdfError):
        ops.parse_pages(spec, 5)


@pytest.mark.parametrize("text,expected", [("a4", (595.3, 841.9)), ("letter", (612, 792)), ("210x297mm", (595.3, 841.9)), ("8.5x11in", (612, 792)), ("595x842", (595, 842))])
def test_page_size_parsing(text, expected):
    w, h = ops.parse_page_size(text)
    assert abs(w - expected[0]) < 0.6 and abs(h - expected[1]) < 0.6


@pytest.mark.parametrize("text", ["a9", "5", "0x0", "axb", "1x1mm"])
def test_bad_page_sizes(text):
    with pytest.raises(ops.PdfError):
        ops.parse_page_size(text)


# ------------------------------------------------------------------------------ jobs
def test_info_is_read_only_and_reports_facts(three):
    before = three.read_bytes()
    res, doc = run_pdf("info", three)
    item = doc["items"][0]
    assert item["pages"] == 3 and item["all_pages_same_size"] is False and item["has_metadata"] and item["metadata"]["Title"] == "Secret Title"
    assert three.read_bytes() == before


def test_merge_keeps_order_and_never_overwrites(tmp_path):
    a = make_pdf(tmp_path / "a.pdf", [(100, 200)])
    b = make_pdf(tmp_path / "b.pdf", [(300, 100), (300, 100)])
    res, doc = run_pdf("merge", a, b)
    out = tmp_path / "a_merged.pdf"
    assert sizes_of(out) == [(100, 200), (300, 100), (300, 100)] and doc["items"][0]["pages"] == 3
    first = out.read_bytes()
    res, doc = run_pdf("merge", a, b)
    assert doc["items"][0]["status"] == "skipped_existing" and out.read_bytes() == first
    res, doc = run_pdf("merge", b, a)                                                              # other order -> other name
    assert doc["items"][0]["status"] == "written" and sizes_of(tmp_path / "b_merged.pdf")[0] == (300, 100)
    res, doc = run_pdf("merge", a, b, "--overwrite")
    assert doc["items"][0]["status"] == "written"
    res, doc = run_pdf("merge", a)
    assert res.exit_code == 1 and "at least two" in doc["items"][0]["error"]


def test_split_every_and_ranges(three, tmp_path):
    res, doc = run_pdf("split", three, "--every", "2", "--out", tmp_path / "s")
    names = sorted(i["output"].rsplit("/", 1)[1] for i in doc["items"])
    assert names == ["doc_p1-2.pdf", "doc_p3-3.pdf"]
    assert [len(PdfReader(i["output"]).pages) for i in sorted(doc["items"], key=lambda i: i["output"])] == [2, 1]
    res, doc = run_pdf("split", three, "--ranges", "1,2-3", "--out", tmp_path / "r")
    assert [i["pages"] for i in doc["items"]] == [1, 2]
    res, doc = run_pdf("split", three, "--every", "1", "--out", tmp_path / "e")
    assert len(doc["items"]) == 3 and all(i["pages"] == 1 for i in doc["items"])
    bad, doc = run_pdf("split", three)
    assert bad.exit_code == 1 and "exactly one" in doc["items"][0]["error"]


def test_extract_pages(three, tmp_path):
    res, doc = run_pdf("extract", three, "--pages", "3,1")
    out = tmp_path / "doc_pages.pdf"
    assert sizes_of(out) == [(300, 400), (300, 400)]
    res, doc = run_pdf("extract", three, "--pages", "9", "--overwrite")
    assert res.exit_code == 1 and "does not exist" in doc["items"][0]["error"]


def test_rotate_selected_pages(three, tmp_path):
    res, doc = run_pdf("rotate", three, "--degrees", "90", "--pages", "1")
    rot = [int(p.get("/Rotate", 0) or 0) for p in PdfReader(str(tmp_path / "doc_rotated.pdf")).pages]
    assert rot == [90, 0, 0] and doc["items"][0]["rotated_pages"] == 1
    res, doc = run_pdf("rotate", three, "--degrees", "45", "--overwrite")
    assert res.exit_code == 1


def test_resize_to_a4_fits_and_centres(three, tmp_path):
    res, doc = run_pdf("resize", three, "--size", "a4")
    out = sizes_of(tmp_path / "doc_resized.pdf")
    assert out[0] == (595, 842) and out[2] == (595, 842)
    assert out[1] == (842, 595)                                  # the landscape page stays landscape


def test_resize_scales_content_not_just_the_page(tmp_path):
    src = make_pdf(tmp_path / "s.pdf", [(100, 100)])
    run_pdf("resize", src, "--size", "200x200")
    page = PdfReader(str(tmp_path / "s_resized.pdf")).pages[0]
    assert (round(float(page.mediabox.width)), round(float(page.mediabox.height))) == (200, 200)


def test_strip_removes_title_and_producer(three, tmp_path):
    res, doc = run_pdf("strip", three)
    out = tmp_path / "doc_stripped.pdf"
    reader = PdfReader(str(out))
    assert not (reader.metadata or {}) and len(reader.pages) == 3
    assert b"Secret Title" not in out.read_bytes() and b"pypdf" not in out.read_bytes()
    assert PdfReader(str(three)).metadata.title == "Secret Title"                  # the original keeps it


@need_rsvg
def test_number_pages_with_format_and_start(three, tmp_path):
    res, doc = run_pdf("number", three, "--format", "Page {n} of {total}", "--start", "1")
    reader = PdfReader(str(tmp_path / "doc_numbered.pdf"))
    texts = [p.extract_text() for p in reader.pages]
    assert "Page 1 of 3" in texts[0] and "Page 2 of 3" in texts[1] and "Page 3 of 3" in texts[2]
    assert sizes_of(tmp_path / "doc_numbered.pdf") == sizes_of(three)
    res, doc = run_pdf("number", three, "--pages", "2", "--format", "{n}", "--start", "10", "--out", tmp_path / "n2.pdf")
    texts = [p.extract_text() for p in PdfReader(str(tmp_path / "n2.pdf")).pages]
    assert texts[0].strip() == "" and "11" in texts[1] and texts[2].strip() == ""


@need_rsvg
@pytest.mark.parametrize("args,fragment", [(["--position", "nowhere"], "position"), (["--size", "2"], "size"), (["--format", "x"], "format")])
def test_number_validation(three, args, fragment):
    res, doc = run_pdf("number", three, *args)
    assert res.exit_code == 1 and fragment in doc["items"][0]["error"]


@need_rsvg
def test_stamp_text_and_logo(three, tmp_path):
    logo = tmp_path / "logo.png"
    Image.new("RGBA", (50, 20), (255, 0, 0, 255)).save(logo)
    res, doc = run_pdf("stamp", three, "--text", "DRAFT", "--angle", "0", "--opacity", "1", "--color", "#00ff00")
    assert res.exit_code == 0
    if shutil.which("gs"):                                                         # the stamp is really on the page: green pixels appear
        subprocess.run(["gs", "-q", "-dNOPAUSE", "-dBATCH", "-sDEVICE=png16m", "-r72", f"-sOutputFile={tmp_path / 's.png'}",
                        "-dFirstPage=1", "-dLastPage=1", str(tmp_path / "doc_stamped.pdf")], check=True)
        subprocess.run(["gs", "-q", "-dNOPAUSE", "-dBATCH", "-sDEVICE=png16m", "-r72", f"-sOutputFile={tmp_path / 'o.png'}",
                        "-dFirstPage=1", "-dLastPage=1", str(three)], check=True)
        import numpy as np

        def green(path):
            a = np.asarray(Image.open(path).convert("RGB")).astype(int)
            return int(((a[..., 1] > 200) & (a[..., 0] < 80) & (a[..., 2] < 80)).sum())

        assert green(tmp_path / "s.png") > green(tmp_path / "o.png") + 200
    assert run_pdf("stamp", three, "--text", "DRAFT", "--angle", "45", "--out", tmp_path / "diag.pdf")[0].exit_code == 0
    res, doc = run_pdf("stamp", three, "--logo", logo, "--opacity", "0.5", "--out", tmp_path / "l.pdf")
    assert res.exit_code == 0 and sizes_of(tmp_path / "l.pdf") == sizes_of(three)
    res, doc = run_pdf("stamp", three, "--overwrite")
    assert res.exit_code == 1 and "needs --text" in doc["items"][0]["error"]
    res, doc = run_pdf("stamp", three, "--text", "x", "--opacity", "2", "--overwrite")
    assert res.exit_code == 1


@need_gs
def test_render_pages_to_images(three, tmp_path):
    res, doc = run_pdf("render", three, "--dpi", "72", "--out", tmp_path / "img")
    assert [i["page"] for i in doc["items"]] == [1, 2, 3]
    assert Image.open(doc["items"][0]["output"]).size == (300, 400) and Image.open(doc["items"][1]["output"]).size == (400, 300)
    res, doc = run_pdf("render", three, "--dpi", "144", "--pages", "2", "--format", "jpeg", "--out", tmp_path / "j")
    assert len(doc["items"]) == 1 and doc["items"][0]["output"].endswith("_p2.jpg") and Image.open(doc["items"][0]["output"]).size == (800, 600)
    assert run_pdf("render", three, "--dpi", "5")[0].exit_code == 1


def test_images_extracts_embedded_pictures(three, tmp_path):
    res, doc = run_pdf("images", three, "--out", tmp_path / "x")
    assert len(doc["items"]) == 3 and all(i["output"].endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff")) for i in doc["items"])
    assert Image.open(doc["items"][0]["output"]).size[0] > 100


def test_from_images_native_and_paper_size(tmp_path):
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    Image.new("RGB", (200, 100), "red").save(a)
    Image.new("RGBA", (100, 200), (0, 0, 255, 0)).save(b)               # fully transparent: flattened on white
    res, doc = run_pdf("from-images", a, b)
    out = tmp_path / "a_images.pdf"
    assert len(PdfReader(str(out)).pages) == 2
    res, doc = run_pdf("from-images", a, b, "--size", "a4", "--dpi", "72", "--out", tmp_path / "p.pdf")
    assert sizes_of(tmp_path / "p.pdf") == [(842, 595), (595, 842)]      # landscape image -> landscape A4
    res, doc = run_pdf("from-images", a, "--overwrite")
    assert (tmp_path / "a.pdf").exists() and doc["items"][0]["pages"] == 1


@need_gs
def test_compress_reports_before_and_after(tmp_path):
    big = tmp_path / "big.pdf"
    Image.effect_noise((900, 900), 40).convert("RGB").save(big, format="PDF", resolution=72.0)
    res, doc = run_pdf("compress", big, "--level", "screen")
    item = doc["items"][0]
    assert res.exit_code == 0 and item["bytes_before"] == big.stat().st_size and item["bytes_after"] > 0
    assert len(PdfReader(item["output"]).pages) == 1
    assert run_pdf("compress", big, "--level", "tiny", "--overwrite")[0].exit_code == 1


# ---------------------------------------------------------------------------- safety
def test_corrupt_and_empty_and_protected_files_are_clean_errors(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4 nonsense")
    res, doc = run_pdf("info", bad)
    assert res.exit_code == 1 and doc["items"][0]["status"] == "failed" and "Traceback" not in res.stdout
    protected = tmp_path / "p.pdf"
    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(100, 100)
    w.encrypt("pw")
    with open(protected, "wb") as fh:
        w.write(fh)
    res, doc = run_pdf("extract", protected, "--pages", "1")
    assert res.exit_code == 1 and "password" in doc["items"][0]["error"]


def test_sandbox_blocks_outputs_and_inputs(three, tmp_path):
    jail = tmp_path / "jail"
    jail.mkdir()
    res = runner.invoke(app, ["--allow", str(jail), "pdf", "info", str(three), "--json"])
    assert res.exit_code == 3
    inside = make_pdf(jail / "in.pdf", [(50, 50)])
    res = runner.invoke(app, ["--allow", str(jail), "pdf", "rotate", str(inside), "--out", str(tmp_path / "out.pdf"), "--json"])
    assert res.exit_code == 3 and not (tmp_path / "out.pdf").exists()


def test_originals_are_never_modified(three, tmp_path):
    before = three.read_bytes()
    for args in (("rotate", three), ("extract", three, "--pages", "1"), ("strip", three), ("resize", three, "--size", "a5")):
        run_pdf(*args)
    assert three.read_bytes() == before


def test_pdf_ops_in_a_pipeline(three, tmp_path):
    from mysuite.pipeline import engine

    pipe = engine.parse({"inputs": ["doc.pdf"], "step": [
        {"tool": "pdf", "action": "extract", "pages": "1,3"},
        {"tool": "pdf", "action": "strip", "only": ["pdf"]},
    ]}, base=tmp_path)
    results = engine.run(pipe)
    assert [r.status for r in results] == ["ok", "ok"] and results[1].outputs[0].endswith("doc_pages_stripped.pdf")
