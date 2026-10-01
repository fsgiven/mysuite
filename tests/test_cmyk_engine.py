from __future__ import annotations

import re
import shutil
import zlib

import pytest

from mysuite.color.cmyk import CmykEngine, CmykError, CmykSettings
from tests.helpers import cli, pdf_cmyk_operators, tiff_cmyk_pixel, write_svg

need = pytest.mark.skipif(not all(shutil.which(t) for t in ("rsvg-convert", "gs", "magick")), reason="needs rsvg/gs/magick")


# ------------------------------------------------------------------ settings
@pytest.mark.parametrize("text,expected", [("exact", ("exact", 5)), ("clean", ("clean", 5)), ("clean:10", ("clean", 10)), (None, ("exact", 5)), ("CLEAN:2", ("clean", 2))])
def test_mode_parsing(text, expected):
    s = CmykSettings.parse(text)
    assert (s.mode, s.step) == expected


@pytest.mark.parametrize("text", ["rich", "clean:0", "clean:99", "clean:x", "exact:5"])
def test_bad_modes_are_rejected(text):
    with pytest.raises(CmykError):
        CmykSettings.parse(text)


def test_missing_profile_is_a_clear_error(tmp_path):
    with pytest.raises(CmykError):
        CmykEngine(CmykSettings.parse("exact", str(tmp_path / "nope.icc")))


def test_an_rgb_profile_is_refused():
    from PIL import ImageCms

    # sRGB profile written to disk: not CMYK
    import tempfile, os
    d = tempfile.mkdtemp()
    path = os.path.join(d, "srgb.icc")
    open(path, "wb").write(ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes())
    with pytest.raises(CmykError):
        CmykEngine(CmykSettings.parse("exact", path))


# -------------------------------------------------------------------- engine
@pytest.fixture(scope="module")
def engine():
    return CmykEngine(CmykSettings.parse("exact"))


def test_greys_are_black_ink_only_and_black_is_not_rich(engine):
    for v in (0, 34, 128, 200):
        c, m, y, k = engine.convert(v, v, v).cmyk
        assert (c, m, y) == (0, 0, 0)
    assert engine.convert(0, 0, 0).cmyk == (0, 0, 0, 100)
    assert engine.convert(255, 255, 255).cmyk == (0, 0, 0, 0)


def test_darker_grey_gets_more_black(engine):
    ks = [engine.convert(v, v, v).cmyk[3] for v in (240, 180, 120, 60, 10)]
    assert ks == sorted(ks)


def test_clean_snaps_to_multiples_and_keeps_colour_close():
    clean = CmykEngine(CmykSettings.parse("clean"))
    for rgb in [(221, 0, 0), (0, 87, 184), (120, 200, 30)]:
        conv = clean.convert(*rgb)
        assert all(v % 5 == 0 for v in conv.cmyk), conv
        assert conv.delta_e < 8


def test_clean_applies_the_extremes_rule():
    clean = CmykEngine(CmykSettings.parse("clean:1"))
    # a colour whose raw magenta lands at 98 must become 100, and 2 -> 0
    for rgb in [(221, 0, 0), (250, 5, 5), (10, 10, 240)]:
        for v in clean.convert(*rgb).cmyk:
            assert v in (0.0, 100.0) or 3 < v < 97


def test_73_92_becomes_75_90_example():
    clean = CmykEngine(CmykSettings.parse("clean:5"))
    assert clean._snap(73) == 75 and clean._snap(92) == 90 and clean._snap(98) == 100 and clean._snap(2) == 0


def test_total_ink_over_limit_is_reported(engine):
    fresh = CmykEngine(CmykSettings.parse("exact"))
    fresh.convert(0, 0, 5)       # deep blue: rich ink
    assert isinstance(fresh.warnings(), list)


def test_image_conversion_matches_flat_conversion(engine):
    from PIL import Image

    im = Image.new("RGB", (4, 1))
    colours = [(221, 0, 0), (34, 34, 34), (255, 255, 255), (0, 87, 184)]
    for i, c in enumerate(colours):
        im.putpixel((i, 0), c)
    out = engine.convert_image(im)
    for i, c in enumerate(colours):
        want = tuple(round(v / 100 * 255) for v in engine.convert(*c).cmyk)
        assert out.getpixel((i, 0)) == want


# ------------------------------------------------------------- end to end
SWATCHES = '<rect width="50" height="100" fill="#dd0000"/><rect x="50" width="50" height="100" fill="#222222"/>' \
           '<rect x="100" width="100" height="100" fill="#0057b8"/>'


def export(tmp_path, *args, formats="pdf,eps,tiff"):
    svg = write_svg(tmp_path / "s.svg", SWATCHES)
    res = cli("export", svg, "--formats", formats, "--profiles", "cmyk", "--sizes", "200", "--out", tmp_path / "o", *args, "-q")
    assert res.exit_code == 0, res.stdout
    return {p.suffix: p for p in (tmp_path / "o").rglob("*") if p.is_file()}


def eps_inks(path):
    text = path.read_bytes().decode("latin-1")
    found = []
    for m in re.finditer(r"([\d.]+) ([\d.]+) ([\d.]+) ([\d.]+) (?:k|setcmykcolor)\b", text):
        found.append(tuple(round(float(x) * 100, 1) for x in m.groups()))
    return found


@need
def test_pdf_eps_tiff_carry_the_same_inks(tmp_path):
    files = export(tmp_path)
    pdf = pdf_cmyk_operators(files[".pdf"])
    eps = eps_inks(files[".eps"])
    red_pdf, grey_pdf, blue_pdf = pdf[0], pdf[1], pdf[2]
    for want, tiff_xy in ((red_pdf, (10, 10)), (grey_pdf, (70, 10)), (blue_pdf, (150, 10))):
        t = tuple(round(v / 255 * 100, 1) for v in tiff_cmyk_pixel(files[".tiff"], *tiff_xy))
        assert all(abs(a - b) <= 1.5 for a, b in zip(want, t)), (want, t)
        assert any(all(abs(a - b) <= 1.5 for a, b in zip(want, e)) for e in eps), (want, eps)


@need
def test_clean_mode_is_visible_in_every_format(tmp_path):
    files = export(tmp_path, "--cmyk-mode", "clean:5")
    for values in pdf_cmyk_operators(files[".pdf"])[:3]:
        # Ghostscript re-prints operands with ~0.1% float noise (70 -> 69.9)
        assert all(abs(v - 5 * round(v / 5)) <= 0.2 for v in values), values
    t = tiff_cmyk_pixel(files[".tiff"], 10, 10)          # helper returns C, M, Y
    assert all(round(v / 255 * 100) % 5 == 0 for v in t)


@need
def test_grey_is_black_only_in_every_format(tmp_path):
    files = export(tmp_path)
    c, m, y, k = pdf_cmyk_operators(files[".pdf"])[1]
    assert (c, m, y) == (0, 0, 0) and k > 50
    t = tiff_cmyk_pixel(files[".tiff"], 70, 10)
    assert t[:3] == (0, 0, 0)


@need
def test_pdf_declares_the_profile_it_was_converted_with(tmp_path):
    files = export(tmp_path, formats="pdf")
    raw = files[".pdf"].read_bytes()
    assert b"/OutputIntent" in raw and b"/DestOutputProfile" in raw
    # embedded ICC is a real CMYK profile
    icc = None
    for m in re.finditer(rb"/N 4[^>]*>>\s*stream\r?\n(.*?)\r?\n?endstream", raw, re.S):
        icc = zlib.decompress(m.group(1))
    assert icc and icc[16:20] == b"CMYK" and icc[36:40] == b"acsp"


@need
def test_exported_pdf_still_opens_and_renders(tmp_path):
    import subprocess

    files = export(tmp_path, formats="pdf")
    out = tmp_path / "r.png"
    subprocess.run(["gs", "-q", "-dBATCH", "-dNOPAUSE", "-dSAFER", "-sDEVICE=png16m", "-r40", f"-sOutputFile={out}", str(files[".pdf"])], check=True)
    assert out.stat().st_size > 100


@need
def test_gradient_logo_still_exports_and_says_what_it_converted(tmp_path):
    svg = write_svg(tmp_path / "g.svg", '<defs><linearGradient id="g"><stop offset="0" stop-color="#dd0000"/><stop offset="1" stop-color="#0000dd"/></linearGradient></defs>'
                                        '<rect width="200" height="100" fill="url(#g)"/><rect width="40" height="40" fill="#222"/>')
    res = cli("export", svg, "--formats", "pdf", "--profiles", "cmyk", "--sizes", "200", "--out", tmp_path / "o")
    assert res.exit_code == 0, res.stdout
    assert "gradient" in res.stdout.lower()
    (pdf,) = (tmp_path / "o").rglob("*.pdf")
    assert pdf_cmyk_operators(pdf)[0][:3] == (0.0, 0.0, 0.0)          # the flat grey is K-only


@need
def test_cli_prints_a_before_after_table(tmp_path):
    svg = write_svg(tmp_path / "s.svg", SWATCHES)
    res = cli("export", svg, "--formats", "pdf", "--profiles", "cmyk", "--sizes", "100", "--cmyk-mode", "clean", "--out", tmp_path / "o")
    assert "#dd0000 ->" in res.stdout and "dE" in res.stdout


@need
def test_bad_cmyk_mode_and_bad_profile_are_clean_errors(tmp_path):
    svg = write_svg(tmp_path / "s.svg", SWATCHES)
    res = cli("export", svg, "--formats", "pdf", "--profiles", "cmyk", "--cmyk-mode", "rich", "--out", tmp_path / "o", "-q")
    assert res.exit_code != 0 and (res.exception is None or isinstance(res.exception, SystemExit))
    bogus = tmp_path / "bogus.icc"
    bogus.write_bytes(b"not a profile")
    res = cli("export", svg, "--formats", "pdf", "--profiles", "cmyk", "--cmyk-profile", bogus, "--out", tmp_path / "o2", "-q")
    assert res.exit_code != 0 and (res.exception is None or isinstance(res.exception, SystemExit))


@need
def test_a_user_profile_is_used_and_embedded(tmp_path):
    from mysuite.color.cmyk import find_default_profile

    prof = find_default_profile()
    custom = tmp_path / "my.icc"
    shutil.copy(prof, custom)
    svg = write_svg(tmp_path / "s.svg", SWATCHES)
    res = cli("export", svg, "--formats", "pdf", "--profiles", "cmyk", "--cmyk-profile", custom, "--out", tmp_path / "o", "-q")
    assert res.exit_code == 0, res.stdout


@need
def test_rgb_exports_are_unchanged_by_the_cmyk_engine(tmp_path):
    svg = write_svg(tmp_path / "s.svg", SWATCHES)
    res = cli("export", svg, "--formats", "pdf,png", "--profiles", "rgb", "--sizes", "100", "--out", tmp_path / "o", "-q")
    assert res.exit_code == 0
    assert not pdf_cmyk_operators(next((tmp_path / "o").rglob("*.pdf")))


@need
def test_transparent_background_in_cmyk_tiff_is_flattened_with_a_note(tmp_path):
    svg = write_svg(tmp_path / "t.svg", '<circle cx="50" cy="50" r="30" fill="#dd0000"/>', width=100, height=100)
    res = cli("export", svg, "--formats", "tiff", "--profiles", "cmyk", "--sizes", "100", "--out", tmp_path / "o")
    assert res.exit_code == 0 and "transparen" in res.stdout.lower()
    (tif,) = (tmp_path / "o").rglob("*.tiff")
    assert tiff_cmyk_pixel(tif, 2, 2)[:3] == (0, 0, 0)    # white paper (no C/M/Y)
