"""Export edge cases and path-safety. Phase 0."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests.helpers import SIMPLE_SVG, cli, identify

pytestmark = pytest.mark.slow
need_tools = pytest.mark.skipif(any(shutil.which(t) is None for t in ("rsvg-convert", "gs", "magick")), reason="needs tools")


@pytest.fixture
def logo(tmp_path):
    p = tmp_path / "logo.svg"
    p.write_text(SIMPLE_SVG)
    return p


def files_outside(root: Path, allowed: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file() and allowed not in p.parents and p.name != "logo.svg"]


@need_tools
@pytest.mark.parametrize("bad", ["0", "-5", "abc", "5xyz", "1e9"])
def test_bad_sizes_are_clean_errors_not_tracebacks(logo, tmp_path, bad):
    res = cli("export", logo, "--sizes", bad, "--out", tmp_path / "o", "--formats", "png", "-q")
    assert res.exit_code != 0
    assert res.exception is None or isinstance(res.exception, SystemExit), repr(res.exception)[:200]


@need_tools
def test_stray_commas_in_a_size_list_are_ignored(logo, tmp_path):
    res = cli("export", logo, "--sizes", "32,,64", "--out", tmp_path / "o", "--formats", "png", "-q")
    assert res.exit_code == 0
    assert sorted(p.stem.split("_")[-1] for p in (tmp_path / "o").rglob("*.png")) == ["32", "64"]


@need_tools
def test_duplicate_sizes_do_not_write_twice_or_crash(logo, tmp_path):
    res = cli("export", logo, "--sizes", "32,32", "--out", tmp_path / "o", "--formats", "png", "-q")
    assert res.exit_code == 0 and len(list((tmp_path / "o").rglob("*.png"))) == 1


@need_tools
def test_enormous_size_is_refused_not_attempted(logo, tmp_path):
    res = cli("export", logo, "--sizes", "200000", "--out", tmp_path / "o", "--formats", "png", "-q")
    assert res.exit_code != 0 or not list((tmp_path / "o").rglob("*.png"))


@need_tools
def test_name_override_cannot_escape_the_output_directory(logo, tmp_path):
    out = tmp_path / "o"
    cli("export", logo, "--name", "../../escaped", "--out", out, "--formats", "png", "--sizes", "32", "-q")
    assert not files_outside(tmp_path, out), files_outside(tmp_path, out)


@need_tools
@pytest.mark.xfail(strict=True, reason="FINDING S2: a naming/path template containing ../ escapes the output directory")
def test_templates_cannot_escape_the_output_directory(logo, tmp_path):
    out = tmp_path / "o"
    cfg = tmp_path / "c.toml"
    cfg.write_text('[export]\nnaming_template = "../../esc_{size}{ext}"\n')
    cli("export", logo, "--config", cfg, "--out", out, "--formats", "png", "--sizes", "32", "-q")
    assert not files_outside(tmp_path, out), files_outside(tmp_path, out)


@need_tools
def test_absolute_path_in_template_is_not_honoured(logo, tmp_path):
    out = tmp_path / "o"
    target = tmp_path / "abs"
    cfg = tmp_path / "c.toml"
    cfg.write_text(f'[export]\nnaming_template = "{target}/x_{{size}}{{ext}}"\n')
    cli("export", logo, "--config", cfg, "--out", out, "--formats", "png", "--sizes", "32", "-q")
    assert not target.exists()


@need_tools
@pytest.mark.xfail(strict=True, reason="FINDING E2: an output path that is a file produces a raw NotADirectoryError traceback")
def test_out_dir_that_is_a_file_is_a_clean_error(logo, tmp_path):
    f = tmp_path / "afile"
    f.write_text("x")
    res = cli("export", logo, "--out", f, "--formats", "png", "--sizes", "32", "-q")
    assert res.exit_code != 0 and (res.exception is None or isinstance(res.exception, SystemExit))


@need_tools
def test_cmyk_pdf_with_background_and_margin_does_not_crash(logo, tmp_path):
    res = cli("export", logo, "--formats", "pdf", "--profiles", "cmyk", "--sizes", "100", "--background", "white", "--margin", "10%",
              "--out", tmp_path / "o", "-q")
    assert res.exit_code == 0


@need_tools
def test_recolor_applies_to_cmyk_outputs_too(logo, tmp_path):
    from tests.helpers import pdf_cmyk_operators
    cli("export", logo, "--formats", "pdf", "--profiles", "cmyk", "--sizes", "100", "--recolor", "#dd0000=#00aa00", "--out", tmp_path / "o", "-q")
    (pdf,) = (tmp_path / "o").rglob("*.pdf")
    first = pdf_cmyk_operators(pdf)[0]
    assert first[0] > 50 and first[1] < 40, first         # green: high cyan, low magenta


@need_tools
def test_extreme_quality_and_png_compression_values_are_validated(logo, tmp_path):
    assert cli("export", logo, "--formats", "jpeg", "--quality", "500", "--sizes", "32", "--out", tmp_path / "o").exit_code != 0
    assert cli("export", logo, "--formats", "png", "--png-compression", "99", "--sizes", "32", "--out", tmp_path / "o").exit_code != 0


@need_tools
def test_input_folder_with_mixed_files_exports_only_svgs(tmp_path):
    d = tmp_path / "in"
    d.mkdir()
    (d / "a.svg").write_text(SIMPLE_SVG)
    (d / "notes.txt").write_text("hi")
    (d / "b.png").write_bytes(b"\x89PNG")
    res = cli("export", d, "--formats", "png", "--sizes", "32", "--out", tmp_path / "o", "-q")
    assert res.exit_code == 0 and [p.parent.parent.parent.name for p in (tmp_path / "o").rglob("*.png")] == ["a"]


@need_tools
@pytest.mark.xfail(strict=True, reason="FINDING D1: export/convert/watermark/metadata refuse to run unless EVERY tool (incl. the macOS-only mysuite-cutout helper and c2patool) is installed")
def test_export_runs_without_the_cutout_helper_and_c2patool(logo, tmp_path, monkeypatch):
    from mysuite import doctor
    from tests.conftest import ORIGINAL_TOOL_SPECS

    monkeypatch.setattr(doctor, "TOOL_SPECS", ORIGINAL_TOOL_SPECS)          # the real, unpatched gate
    cfg = tmp_path / "c.toml"
    cfg.write_text('[tools]\ncutout_tool = "mysuite-cutout-NOT-INSTALLED"\nc2patool = "c2patool-NOT-INSTALLED"\n')
    res = cli("export", logo, "--config", cfg, "--formats", "png", "--sizes", "16", "--out", tmp_path / "o", "-q")
    assert res.exit_code == 0 and len(list((tmp_path / "o").rglob("*.png"))) == 1
