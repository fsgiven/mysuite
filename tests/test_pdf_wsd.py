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
