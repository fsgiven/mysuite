"""SVG import (PDF / AI / EPS / SVGZ) and the SVG optimiser — verified by rendering, not by eyeballing."""
from __future__ import annotations

import gzip
import json
import shutil
import subprocess

import numpy as np
import pytest
from PIL import Image

from mysuite.config import ToolPaths
from mysuite.vector import imports as imp
from mysuite.vector.svgopt import OptimiseError, optimise
from mysuite.vector.verify import renders_same
from tests.helpers import SIMPLE_SVG, cli

TOOLS = ToolPaths()
need_render = pytest.mark.skipif(shutil.which("rsvg-convert") is None, reason="needs rsvg-convert")
need_poppler = pytest.mark.skipif(any(shutil.which(t) is None for t in ("pdftocairo", "gs", "rsvg-convert")), reason="needs poppler, gs, rsvg-convert")

MESSY = """<?xml version="1.0" encoding="UTF-8"?>
<!-- Generator: Adobe Illustrator -->
<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">
<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"
     xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd" version="1.1" x="0px" y="0px" viewBox="0 0 200 100"
     sodipodi:docname="logo.svg" enable-background="new 0 0 200 100">
  <metadata><rdf>junk</rdf></metadata>
  <title>My logo</title>
  <defs>
    <linearGradient id="grad"><stop offset="0.000000" stop-color="rgb(255, 0, 0)"/><stop offset="1.0" stop-color="#0000ff"/></linearGradient>
    <linearGradient id="never-used"><stop offset="0" stop-color="#123456"/></linearGradient>
    <clipPath id="clip"><rect width="150.000001" height="100"/></clipPath>
  </defs>
  <g id="layer1" inkscape:groupmode="layer">
    <g>
      <rect id="bg" x="0.0000" y="0" width="200.0000" height="100.0000" fill="url(#grad)" opacity="1"/>
      <circle cx="100.123456" cy="50.5" r="30.00004" fill="#ffffff" stroke="#003366" stroke-width="2.50000"/>
      <path d="M 10.000000 10.0000 L 60.123456789 10 L 60 60.987654321 Z" fill="rgb(221,0,0)" clip-path="url(#clip)"/>
      <path d="M10,90 a20,20 0 011 40,0" fill="none" stroke="#000"/>
      <g/>
    </g>
  </g>
</svg>
"""


def test_optimise_removes_junk_but_keeps_what_is_referenced():
    result = optimise(MESSY)
    text = result.text
    assert result.after < result.before * 0.6
    for gone in ("Illustrator", "DOCTYPE", "metadata", "inkscape", "sodipodi", "never-used", "enable-background", "<g/>", 'version='):
        assert gone not in text, gone
    for kept in ('id="grad"', 'id="clip"', "url(#grad)", "url(#clip)", "<title>My logo</title>"):
        assert kept in text, kept
    assert 'fill="#d00"' in text and 'stop-color="#f00"' in text and 'fill="#fff"' in text        # shortest hex
    assert 'stroke="#036"' in text and 'stroke="#000"' in text
    assert "opacity" not in text.replace("stroke-opacity", "")
    assert "60.123" in text and "100.123" in text and "30.00004" not in text
    assert 'id="bg"' not in text and 'id="layer1"' not in text                                     # ids nobody points at
    assert "a20,20 0 011 40,0" in text                                                           # arc flags packed: kept exact


def test_options_keep_ids_and_titles_as_asked():
    kept = optimise(MESSY, keep_ids=True).text
    assert 'id="bg"' in kept and 'id="never-used"' in kept
    assert "<title>" not in optimise(MESSY, keep_title=False).text
    assert "60.12" in optimise(MESSY, precision=2).text and "60.123" not in optimise(MESSY, precision=2).text


def test_small_viewboxes_keep_extra_precision():
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><path d="M0.1234567 0.5 L0.98765 0.5"/></svg>'
    assert 'd="M0.12346 0.5 L0.98765 0.5"' in optimise(svg).text          # 3 + 2 decimals, not 3
    big = svg.replace('viewBox="0 0 1 1"', 'viewBox="0 0 500 500"')
    assert 'd="M0.123 0.5 L0.988 0.5"' in optimise(big).text


def test_text_whitespace_and_xml_space_survive():
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 20"><text xml:space="preserve" x="0" y="10">a  b <tspan> c</tspan> </text></svg>'
    out = optimise(svg).text
    assert "a  b " in out and "> c</tspan>" in out and "preserve" in out


def test_refuses_entities_and_non_svg_and_leaves_scripted_files_alone():
    with pytest.raises(OptimiseError, match="entities"):
        optimise('<!DOCTYPE svg [<!ENTITY x "boom">]><svg xmlns="http://www.w3.org/2000/svg">&x;</svg>')
    with pytest.raises(OptimiseError, match="not valid XML"):
        optimise("<svg")
    with pytest.raises(OptimiseError, match="not <svg>"):
        optimise("<html/>")
    scripted = '<svg xmlns="http://www.w3.org/2000/svg"><script>go("a")</script><defs><g id="a"/></defs><rect id="b" width="1" height="1"/></svg>'
    out = optimise(scripted)
    assert 'id="b"' in out.text and any("script" in n for n in out.notes)


@need_render
@pytest.mark.parametrize("svg", [MESSY, SIMPLE_SVG, '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><g transform="rotate(33.3333333 50 50) translate(10.123456 5)"><rect width="40.0001" height="30" fill="rgba(255,0,0,0.5)"/></g></svg>'])
def test_optimised_files_render_the_same(svg):
    same, mean, edge = renders_same(svg, optimise(svg).text, TOOLS)
    assert same, (mean, edge)


@need_render
def test_verify_notices_a_real_difference():
    other = SIMPLE_SVG.replace("#dd0000", "#00dd00")
    same, mean, _ = renders_same(SIMPLE_SVG, other, TOOLS)
    assert not same and mean > 1


@need_render
def test_optimise_command_writes_beside_and_reports_savings(tmp_path):
    src = tmp_path / "logo.svg"
    src.write_text(MESSY)
    result = cli("svg", "optimise", src, "--json")
    assert result.exit_code == 0, result.output
    doc = json.loads(result.stdout)
    item = doc["items"][0]
    assert item["output"].endswith("logo.min.svg") and item["bytes_after"] < item["bytes_before"] and doc["saved_percent"] > 30
    assert any("verified" in n for n in item["notes"]) and src.read_text() == MESSY                 # original untouched
    again = cli("svg", "optimise", src, "--json")
    assert json.loads(again.stdout)["items"][0]["status"] == "skipped_existing"
    assert cli("svg", "optimise", src, "--overwrite", "--svgz", "--json").exit_code == 0
    assert gzip.decompress((tmp_path / "logo.min.svgz").read_bytes()).startswith(b"<svg")


@need_render
def test_optimise_command_refuses_to_write_something_that_looks_different(tmp_path, monkeypatch):
    from mysuite.vector import cli as vcli
    from mysuite.vector.svgopt import OptimiseResult

    src = tmp_path / "logo.svg"
    src.write_text(SIMPLE_SVG)
    monkeypatch.setattr(vcli, "optimise", lambda text, **kw: OptimiseResult(SIMPLE_SVG.replace("#dd0000", "#00ff00"), len(text), len(text)))
    result = cli("svg", "optimise", src, "--json")
    doc = json.loads(result.stdout)
    assert result.exit_code == 1 and doc["items"][0]["status"] == "failed" and "look different" in doc["items"][0]["error"]
    assert not (tmp_path / "logo.min.svg").exists()


def test_optimise_dry_run_and_sandbox(tmp_path):
    src = tmp_path / "logo.svg"
    src.write_text(MESSY)
    doc = json.loads(cli("svg", "optimise", src, "--no-verify", "--dry-run", "--json").stdout)
    assert doc["items"][0]["status"] == "planned" and not (tmp_path / "logo.min.svg").exists()
    bad = cli("--allow", tmp_path / "elsewhere", "svg", "optimise", src, "--no-verify", "--json")
    assert bad.exit_code == 3


# --------------------------------------------------------------------------------------------- import
def _make_sources(tmp_path):
    svg = tmp_path / "logo.svg"
    svg.write_text(SIMPLE_SVG)
    pdf = tmp_path / "logo.pdf"
    subprocess.run(["rsvg-convert", "-f", "pdf", "-o", str(pdf), str(svg)], check=True)
    ai = tmp_path / "brand.ai"
    shutil.copy(pdf, ai)                                         # modern .ai files ARE PDFs
    eps = tmp_path / "old.eps"
    subprocess.run(["rsvg-convert", "-f", "eps", "-o", str(eps), str(svg)], check=True)
    return svg, pdf, ai, eps


def _render(svg_path, width=200):
    png = svg_path.with_suffix(".check.png")
    subprocess.run(["rsvg-convert", "-w", str(width), "-b", "white", "-o", str(png), str(svg_path)], check=True)
    return np.asarray(Image.open(png).convert("RGB"), dtype=np.int16)


@need_poppler
@pytest.mark.parametrize("which", ["pdf", "ai", "eps"])
def test_import_gives_an_svg_that_looks_like_the_original(tmp_path, which):
    svg, pdf, ai, eps = _make_sources(tmp_path)
    source = {"pdf": pdf, "ai": ai, "eps": eps}[which]
    result = imp.to_svg(source, tmp_path / "out", tools=TOOLS)
    assert result.svg.name == f"{source.stem}.svg"
    # compare against the original rendered at the same width; the PDF page is trimmed to the drawing
    a, b = _render(svg), _render(result.svg)
    assert a.shape == b.shape or abs(a.shape[0] - b.shape[0]) <= 3
    rows = min(a.shape[0], b.shape[0])
    assert np.abs(a[:rows] - b[:rows]).mean() < 8, np.abs(a[:rows] - b[:rows]).mean()


@need_poppler
def test_import_trims_a_logo_off_a_big_page_and_no_crop_keeps_the_page(tmp_path):
    svg, pdf, *_ = _make_sources(tmp_path)
    small = tmp_path / "small.svg"
    small.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="400" height="400" viewBox="0 0 400 400"><rect x="150" y="150" width="50" height="50" fill="#123456"/></svg>')
    page = tmp_path / "page.pdf"
    subprocess.run(["rsvg-convert", "-f", "pdf", "-o", str(page), str(small)], check=True)
    cropped = imp.to_svg(page, tmp_path / "c", tools=TOOLS).svg.read_text()
    whole = imp.to_svg(page, tmp_path / "w", tools=TOOLS, crop=False).svg.read_text()
    assert 'viewBox="0 0 300 300"' in whole or 'viewBox="0 0 ' in whole
    import re
    w = float(re.search(r'viewBox="[\d.]+ [\d.]+ ([\d.]+) ', cropped).group(1))
    assert 36 < w < 40 and "crop" not in cropped                                              # 50 px = 37.5 pt


@need_poppler
def test_import_svgz_pages_and_errors(tmp_path):
    svgz = tmp_path / "logo.svgz"
    svgz.write_bytes(gzip.compress(SIMPLE_SVG.encode()))
    assert imp.to_svg(svgz, tmp_path / "z", tools=TOOLS).svg.read_text() == SIMPLE_SVG
    bomb = tmp_path / "bomb.svgz"
    bomb.write_bytes(gzip.compress(b"x" * (imp.MAX_UNZIPPED_BYTES + 10)))
    with pytest.raises(imp.ImportError_, match="refusing"):
        imp.to_svg(bomb, tmp_path / "b", tools=TOOLS)
    junk = tmp_path / "junk.pdf"
    junk.write_text("hello")
    with pytest.raises(imp.ImportError_, match="not a PDF"):
        imp.to_svg(junk, tmp_path / "j", tools=TOOLS)
    pages = [Image.new("RGB", (100, 100), c) for c in ("red", "blue")]
    two = tmp_path / "two.pdf"
    pages[0].save(two, format="PDF", save_all=True, append_images=pages[1:])
    with pytest.raises(imp.ImportError_, match="2 page"):
        imp.to_svg(two, tmp_path / "p", page=3, tools=TOOLS)
    result = imp.to_svg(two, tmp_path / "p", page=2, tools=TOOLS)
    assert result.pages == 2 and any("2 pages" in n for n in result.notes) and any("raster" in n for n in result.notes)


@need_poppler
def test_import_command_json_overwrite_and_all_pages(tmp_path):
    svg, pdf, ai, eps = _make_sources(tmp_path)
    doc = json.loads(cli("svg", "import", ai, eps, "--json").stdout)
    assert [i["status"] for i in doc["items"]] == ["written", "written"] and (tmp_path / "brand.svg").exists() and (tmp_path / "old.svg").exists()
    text = (tmp_path / "brand.svg").read_text()
    assert "#d00" in text and "rgb(" not in text                                              # percent colours became hex
    assert json.loads(cli("svg", "import", ai, "--json").stdout)["items"][0]["status"] == "skipped_existing"
    pages = [Image.new("RGB", (100, 100), c) for c in ("red", "blue", "green")]
    multi = tmp_path / "multi.pdf"
    pages[0].save(multi, format="PDF", save_all=True, append_images=pages[1:])
    doc = json.loads(cli("svg", "import", multi, "--all-pages", "--out", tmp_path / "pages", "--json").stdout)
    assert sorted(p.name for p in (tmp_path / "pages").glob("*.svg")) == ["multi-1.svg", "multi-2.svg", "multi-3.svg"] and doc["ok"]
    dry = json.loads(cli("svg", "import", ai, "--dry-run", "--overwrite", "--json").stdout)
    assert dry["items"][0]["status"] == "planned"
    bad = tmp_path / "bad.ai"
    bad.write_text("not an illustrator file")
    result = cli("svg", "import", bad, "--json")
    assert result.exit_code == 1 and json.loads(result.stdout)["items"][0]["status"] == "failed"


@need_poppler
def test_export_takes_ai_pdf_eps_svgz_directly_and_recolor_still_works(tmp_path):
    from tests.helpers import pixel_rgb

    svg, pdf, ai, eps = _make_sources(tmp_path)
    out = tmp_path / "out"
    result = cli("export", ai, "--formats", "png", "--sizes", "120", "--out", out, "--recolor", "#dd0000=#00aa00", "--json")
    assert result.exit_code == 0, result.output
    doc = json.loads(result.stdout)
    assert doc["items"][0]["input"].endswith("brand.ai") and doc["items"][0]["output"].endswith("brand_120.png")
    (png,) = out.rglob("*.png")
    r, g, b = pixel_rgb(png, 4, 4)
    assert g > 140 and r < 40                                                                  # the red square turned green
    assert cli("export", eps, "--formats", "png", "--sizes", "50", "--out", tmp_path / "o2", "--json").exit_code == 0
    z = tmp_path / "z.svgz"
    z.write_bytes(gzip.compress(SIMPLE_SVG.encode()))
    assert cli("export", z, "--formats", "png", "--sizes", "50", "--out", tmp_path / "o3", "--json").exit_code == 0


@need_poppler
def test_pipeline_can_import_then_optimise(tmp_path):
    svg, pdf, ai, eps = _make_sources(tmp_path)
    plan = tmp_path / "p.toml"
    plan.write_text(f"""
inputs = ["{ai}"]

[[step]]
tool = "svg"
action = "import"

[[step]]
tool = "svg"
action = "optimise"
verify = false
from = "previous"
""")
    result = cli("pipeline", "run", plan, "--json")
    assert result.exit_code == 0, result.output
    assert (tmp_path / "brand.svg").exists() and (tmp_path / "brand.min.svg").exists()
    bad = tmp_path / "bad.toml"
    bad.write_text('inputs = ["x"]\n[[step]]\ntool = "svg"\naction = "explode"\n')
    refused = cli("pipeline", "run", bad, "--json")
    assert refused.exit_code != 0 and "svg needs action" in refused.stdout
