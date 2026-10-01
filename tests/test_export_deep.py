"""In-depth Export tests (phase 0). Tests marked xfail(strict=True) document a
known bug found by this pass; see docs/TEST-FINDINGS.md. strict=True means the
suite fails loudly the day the bug is fixed, so the marker can be removed."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tests.helpers import (
    SIMPLE_SVG, cli, identify, pdf_cmyk_operators, pdf_has_rgb_operators, pixel_rgb, tiff_cmyk_pixel, write_svg,
)

pytestmark = pytest.mark.slow
need_tools = pytest.mark.skipif(
    any(shutil.which(t) is None for t in ("rsvg-convert", "gs", "magick")), reason="needs rsvg-convert, gs, magick"
)
pytest.importorskip("typer")


@pytest.fixture
def logo(tmp_path):
    p = tmp_path / "logo.svg"
    p.write_text(SIMPLE_SVG)
    return p


def run_export(logo: Path, out: Path, *args):
    res = cli("export", logo, "--out", out, *args)
    return res


# ---------------------------------------------------------------- format grid
@need_tools
@pytest.mark.parametrize("fmt,magic", [
    ("png", b"\x89PNG"), ("pdf", b"%PDF"), ("eps", b"%!PS"), ("svg", b"<"), ("jpeg", b"\xff\xd8"),
    ("webp", b"RIFF"), ("tiff", b"II"),
])
def test_every_single_file_format_produces_a_valid_file_rgb(logo, tmp_path, fmt, magic):
    res = run_export(logo, tmp_path / "o", "--formats", fmt, "--sizes", "64", "-q")
    assert res.exit_code == 0, res.stdout
    (f,) = (tmp_path / "o").rglob(f"*_64.*")
    head = f.read_bytes()[:4]
    assert head.startswith(magic) or (fmt == "tiff" and head[:2] in (b"II", b"MM")), (fmt, head)
    if fmt not in ("pdf", "eps", "svg"):
        assert identify(f, "%wx%h") in ("64x64", "64x32"), identify(f, "%wx%h")


@need_tools
def test_ico_bundle_contains_every_requested_size(logo, tmp_path):
    res = run_export(logo, tmp_path / "o", "--formats", "ico", "--sizes", "16,32,48", "-q")
    assert res.exit_code == 0, res.stdout
    (ico,) = (tmp_path / "o").rglob("*.ico")
    sizes = sorted(set(identify(ico, "%w ").split()))
    assert sizes == ["16", "32", "48"], sizes


@need_tools
@pytest.mark.skipif(shutil.which("iconutil") is None, reason="macOS iconutil")
def test_icns_bundle_is_valid_and_ignores_sizes_flag(logo, tmp_path):
    res = run_export(logo, tmp_path / "o", "--formats", "icns", "--sizes", "20", "-q")
    assert res.exit_code == 0, res.stdout
    (icns,) = (tmp_path / "o").rglob("*.icns")
    assert icns.read_bytes()[:4] == b"icns" and icns.stat().st_size > 5000


# ------------------------------------------------------------------------ CMYK
@need_tools
def test_cmyk_only_for_print_formats_and_png_cmyk_is_skipped_with_a_warning(logo, tmp_path):
    res = run_export(logo, tmp_path / "o", "--formats", "png,pdf", "--profiles", "rgb,cmyk", "--sizes", "64")
    assert res.exit_code == 0
    names = sorted(p.relative_to(tmp_path / "o").as_posix() for p in (tmp_path / "o").rglob("*.*"))
    assert any("pdf/cmyk" in n for n in names) and not any("png/cmyk" in n for n in names)
    assert "skipping" in res.stdout.lower() or "png" in res.stdout.lower()


@need_tools
def test_strict_turns_the_cmyk_skip_into_an_error(logo, tmp_path):
    res = run_export(logo, tmp_path / "o", "--formats", "png", "--profiles", "cmyk", "--strict", "--sizes", "64")
    assert res.exit_code != 0


@need_tools
def test_cmyk_pdf_stores_only_cmyk_not_rgb_operators(logo, tmp_path):
    run_export(logo, tmp_path / "o", "--formats", "pdf", "--profiles", "cmyk", "--sizes", "100", "-q")
    (pdf,) = (tmp_path / "o").rglob("*.pdf")
    assert pdf_cmyk_operators(pdf) and not pdf_has_rgb_operators(pdf)


@need_tools
def test_cmyk_tiff_really_is_cmyk(logo, tmp_path):
    run_export(logo, tmp_path / "o", "--formats", "tiff", "--profiles", "cmyk", "--sizes", "100", "-q")
    (tif,) = (tmp_path / "o").rglob("*.tiff")
    assert identify(tif, "%[colorspace]") == "CMYK"


@need_tools
@pytest.mark.xfail(strict=True, reason="FINDING C1: pdf (Ghostscript) and tiff (ImageMagick) convert RGB->CMYK with different engines, so one brand red gets different inks per format")
def test_same_colour_gets_the_same_ink_in_pdf_and_tiff(logo, tmp_path):
    run_export(logo, tmp_path / "o", "--formats", "pdf,tiff", "--profiles", "cmyk", "--sizes", "100", "-q")
    (pdf,) = (tmp_path / "o").rglob("*.pdf")
    (tif,) = (tmp_path / "o").rglob("*.tiff")
    pdf_red = pdf_cmyk_operators(pdf)[0]            # first fill = #dd0000
    # tiff is 100px wide for a 200x100 viewBox square -> left swatch at x~25
    t = tiff_cmyk_pixel(tif, 20, 20)
    tiff_red = tuple(round(v / 255 * 100, 1) for v in t)
    assert all(abs(a - b) <= 3 for a, b in zip(pdf_red[:3], tiff_red[:3])), (pdf_red, tiff_red)


@need_tools
@pytest.mark.xfail(strict=True, reason="FINDING C2: neutral grey #222222 becomes a 'rich' CMYK mix (~69/66/65/72) in PDF/EPS instead of K-only")
def test_neutral_grey_stays_black_ink_only_in_cmyk_pdf(tmp_path):
    svg = write_svg(tmp_path / "g.svg", '<rect width="200" height="100" fill="#222222"/>')
    run_export(svg, tmp_path / "o", "--formats", "pdf", "--profiles", "cmyk", "--sizes", "100", "-q")
    (pdf,) = (tmp_path / "o").rglob("*.pdf")
    c, m, y, k = pdf_cmyk_operators(pdf)[0]
    assert (c, m, y) == (0, 0, 0) or max(c, m, y) < 3, (c, m, y, k)


@need_tools
@pytest.mark.xfail(strict=True, reason="FINDING C3: no ICC profile / output intent is embedded, so the CMYK numbers have no defined meaning for a print shop")
def test_cmyk_pdf_declares_a_colour_profile_or_output_intent(logo, tmp_path):
    run_export(logo, tmp_path / "o", "--formats", "pdf", "--profiles", "cmyk", "--sizes", "100", "-q")
    (pdf,) = (tmp_path / "o").rglob("*.pdf")
    raw = pdf.read_bytes()
    assert b"OutputIntent" in raw or b"ICCBased" in raw


@need_tools
def test_cmyk_eps_is_cmyk_not_rgb(logo, tmp_path):
    run_export(logo, tmp_path / "o", "--formats", "eps", "--profiles", "cmyk", "--sizes", "100", "-q")
    (eps,) = (tmp_path / "o").rglob("*.eps")
    text = eps.read_bytes().decode("latin-1")
    assert " setcmykcolor" in text or " k\n" in text or "DeviceCMYK" in text or " cmyk" in text.lower()


# --------------------------------------------------------------------- geometry
@need_tools
def test_size_means_width_and_aspect_ratio_is_kept(logo, tmp_path):
    run_export(logo, tmp_path / "o", "--formats", "png", "--sizes", "100", "-q")
    (png,) = (tmp_path / "o").rglob("*.png")
    w, h = (int(v) for v in identify(png, "%w %h").split())
    assert (w, h) == (100, 50), "size is the width; height follows the 2:1 artwork"
    left, right = pixel_rgb(png, 10, 25), pixel_rgb(png, 90, 25)
    assert left[0] > 180 and right[2] > 130            # red left, blue right survive


@need_tools
def test_margin_shrinks_artwork_and_background_flattens_it(logo, tmp_path):
    run_export(logo, tmp_path / "o", "--formats", "png", "--sizes", "100", "--margin", "20%", "--background", "white", "-q")
    (png,) = (tmp_path / "o").rglob("*.png")
    assert pixel_rgb(png, 2, 2) == (255, 255, 255)
    assert pixel_rgb(png, 30, 50)[0] > 180


@need_tools
@pytest.mark.parametrize("sizes,names", [("32,64", ["32", "64"]), ("2cm", ["2cm"]), ("1in", ["1in"])])
def test_sizes_units_and_names(logo, tmp_path, sizes, names):
    res = run_export(logo, tmp_path / "o", "--formats", "png", "--sizes", sizes, "--dpi", "100", "-q")
    assert res.exit_code == 0, res.stdout
    found = sorted(p.stem.split("_")[-1] for p in (tmp_path / "o").rglob("*.png"))
    assert found == sorted(names)


@need_tools
def test_physical_size_uses_dpi(logo, tmp_path):
    run_export(logo, tmp_path / "o", "--formats", "png", "--sizes", "1in", "--dpi", "150", "-q")
    (png,) = (tmp_path / "o").rglob("*.png")
    assert identify(png, "%w") == "150"


# ------------------------------------------------------------ svg input quirks
@need_tools
@pytest.mark.parametrize("name,svg", [
    ("no_viewbox", lambda p: write_svg(p, '<rect width="200" height="100" fill="#dd0000"/>', viewbox=False)),
    ("no_size_attrs", lambda p: p.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 50 50"><circle cx="25" cy="25" r="20" fill="#0a0"/></svg>') or p),
    ("huge_viewbox", lambda p: write_svg(p, '<rect width="50000" height="50000" fill="#00f"/>', width=50000, height=50000)),
    ("tiny_viewbox", lambda p: write_svg(p, '<rect width="2" height="2" fill="#f0f"/>', width=2, height=2)),
    ("gradient", lambda p: write_svg(p, '<defs><linearGradient id="g"><stop offset="0" stop-color="#f00"/><stop offset="1" stop-color="#00f"/></linearGradient></defs><rect width="200" height="100" fill="url(#g)"/>')),
    ("use_and_defs", lambda p: write_svg(p, '<defs><rect id="r" width="50" height="50" fill="#0a0"/></defs><use href="#r"/><use href="#r" x="100"/>')),
    ("text_missing_font", lambda p: write_svg(p, '<text x="10" y="50" font-family="NoSuchFont123" font-size="30">Logo</text>')),
    ("clip_and_opacity", lambda p: write_svg(p, '<clipPath id="c"><circle cx="50" cy="50" r="40"/></clipPath><rect width="200" height="100" fill="#f00" opacity=".5" clip-path="url(#c)"/>')),
])
def test_unusual_but_valid_svgs_export_without_error(tmp_path, name, svg):
    p = tmp_path / f"{name}.svg"
    svg(p)
    res = run_export(p, tmp_path / "o", "--formats", "png,pdf", "--sizes", "64", "-q")
    assert res.exit_code == 0, (name, res.stdout[-300:])
    assert len(list((tmp_path / "o").rglob("*.png"))) == 1


@need_tools
def test_malformed_svg_fails_cleanly_without_a_traceback(tmp_path):
    p = tmp_path / "bad.svg"
    p.write_text("<svg xmlns='http://www.w3.org/2000/svg'><rect")
    res = run_export(p, tmp_path / "o", "--formats", "png", "--sizes", "64", "-q")
    assert res.exit_code != 0
    assert res.exception is None or isinstance(res.exception, SystemExit)


@need_tools
def test_external_file_references_in_svg_are_not_followed(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET-CONTENT")
    p = tmp_path / "x.svg"
    p.write_text(
        f'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY xxe SYSTEM "file://{secret}">]>'
        '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="50"><text x="5" y="30">&xxe;</text></svg>'
    )
    res = run_export(p, tmp_path / "o", "--formats", "svg", "--sizes", "64", "-q")
    for f in (tmp_path / "o").rglob("*"):
        if f.is_file():
            assert b"TOP-SECRET-CONTENT" not in f.read_bytes(), f"XXE leaked into {f.name}"


# ------------------------------------------------------- naming / collisions
@need_tools
def test_default_layout_and_custom_name_variant_and_date_stamp(logo, tmp_path):
    run_export(logo, tmp_path / "o", "--formats", "png", "--sizes", "32", "--name", "acme", "--variant", "negative", "--date-stamp", "-q")
    (png,) = (tmp_path / "o").rglob("*.png")
    rel = png.relative_to(tmp_path / "o").as_posix()
    assert rel.startswith("acme/") and "negative" in rel and rel.count("/") >= 3
    assert any(ch.isdigit() for ch in png.stem.split("_")[-1]) 


@need_tools
def test_two_inputs_with_the_same_stem_stop_before_writing(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    (a / "icon.svg").write_text(SIMPLE_SVG); (b / "icon.svg").write_text(SIMPLE_SVG)
    res = cli("export", a / "icon.svg", b / "icon.svg", "--out", tmp_path / "o", "--formats", "png", "--sizes", "32")
    assert res.exit_code != 0
    assert not (tmp_path / "o").exists() or not list((tmp_path / "o").rglob("*.png"))


@need_tools
def test_existing_outputs_are_skipped_unless_overwrite(logo, tmp_path):
    run_export(logo, tmp_path / "o", "--formats", "png", "--sizes", "32", "-q")
    (png,) = (tmp_path / "o").rglob("*.png")
    png.write_bytes(b"sentinel")
    run_export(logo, tmp_path / "o", "--formats", "png", "--sizes", "32", "-q")
    assert png.read_bytes() == b"sentinel"
    run_export(logo, tmp_path / "o", "--formats", "png", "--sizes", "32", "--overwrite", "-q")
    assert png.read_bytes() != b"sentinel"


@need_tools
def test_dry_run_writes_nothing(logo, tmp_path):
    res = run_export(logo, tmp_path / "o", "--formats", "png,pdf", "--sizes", "32,64", "--dry-run")
    assert res.exit_code == 0 and not (tmp_path / "o").exists()


# ----------------------------------------------------------------------- recolor
RECOLOR_CASES = [
    ("hex6", '<rect width="9" height="9" fill="#dd0000"/>', None),
    ("hex6-upper", '<rect width="9" height="9" fill="#DD0000"/>', None),
    ("style-attr", '<rect width="9" height="9" style="fill:#dd0000"/>', None),
    ("stop-color", '<linearGradient id="g"><stop stop-color="#dd0000"/></linearGradient><rect width="9" height="9" fill="url(#g)"/>', None),
    ("css-block-hex", '<style>.a{fill:#dd0000}</style><rect class="a" width="9" height="9"/>', None),
    ("hex3-short", '<rect width="9" height="9" fill="#d00"/>', None),
    ("rgb()", '<rect width="9" height="9" fill="rgb(221,0,0)"/>', None),
    ("rgb-percent", '<rect width="9" height="9" fill="rgb(86.7%,0%,0%)"/>', None),
    ("hsl()", '<rect width="9" height="9" fill="hsl(0,100%,43.3%)"/>', None),
    ("named-in-css-block", '<style>.a{fill:red}</style><rect class="a" width="9" height="9"/>', None),
    ("near-identical", '<rect width="9" height="9" fill="#dc0100"/>', None),
]


@pytest.mark.parametrize("name,body,finding", RECOLOR_CASES, ids=[c[0] for c in RECOLOR_CASES])
def test_recolor_matrix(tmp_path, name, body, finding):
    from mysuite.export.recolor import apply_recolor

    p = write_svg(tmp_path / "t.svg", body)
    recolor = {"#dd0000": "#0000ff"} if "named" not in name else {"red": "blue"}
    out, _ = apply_recolor(p, recolor)
    text = out.read_text().lower()
    ok = "0000ff" in text or "blue" in text

    if finding:
        if ok:
            pytest.fail(f"{finding} — but it now works; remove the xfail expectation")
        pytest.xfail(finding)
    assert ok


def test_recolor_does_not_touch_longer_hex_runs_or_ids(tmp_path):
    from mysuite.export.recolor import apply_recolor

    p = write_svg(tmp_path / "t.svg", '<rect id="dd0000x" fill="#dd0000ff"/><rect fill="#dd0000"/>')
    out, _ = apply_recolor(p, {"#dd0000": "#0000ff"})
    text = out.read_text()
    assert 'id="dd0000x"' in text and "#dd0000ff" in text and text.count("#0000ff") == 1


def test_recolor_through_the_cli_changes_pixels(logo, tmp_path):
    res = run_export(logo, tmp_path / "o", "--formats", "png", "--sizes", "100", "--recolor", "#dd0000=#00aa00", "-q")
    assert res.exit_code == 0, res.stdout
    (png,) = (tmp_path / "o").rglob("*.png")
    r, g, b = pixel_rgb(png, 10, 50)
    assert g > 120 and r < 60
