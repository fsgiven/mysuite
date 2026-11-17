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
