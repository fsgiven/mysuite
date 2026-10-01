from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from mysuite.cli import app
from tests.helpers import SIMPLE_SVG, cli, runner

need = pytest.mark.skipif(not all(shutil.which(t) for t in ("rsvg-convert", "gs", "magick", "exiftool")), reason="needs rsvg/gs/magick/exiftool")


def run_json(*args):
    res = runner.invoke(app, [str(a) for a in args])
    try:
        doc = json.loads(res.stdout)          # stdout must be exactly one JSON document
    except json.JSONDecodeError as exc:       # pragma: no cover - failure message
        raise AssertionError(f"stdout is not one JSON document: {res.stdout!r} (stderr={res.stderr!r})") from exc
    return res, doc


def check_envelope(doc, command, exit_code=0):
    assert doc["schema_version"] == 1
    assert doc["command"] == command
    assert doc["exit_code"] == exit_code and doc["ok"] == (exit_code == 0)
    for key in ("items", "warnings", "errors"):
        assert isinstance(doc[key], list)


@pytest.fixture
def png(tmp_path):
    p = tmp_path / "a.png"
    subprocess.run(["magick", "-size", "40x30", "xc:#dd0000", f"PNG24:{p}"], check=True)
    return p


@pytest.fixture
def svg(tmp_path):
    p = tmp_path / "logo.svg"
    p.write_text(SIMPLE_SVG)
    return p


@need
def test_convert_json_written_then_skipped(png):
    res, doc = run_json("convert", png, "--to", "jpeg", "--json")
    check_envelope(doc, "convert")
    assert doc["items"][0]["status"] == "written" and doc["items"][0]["output"].endswith("a.jpg")
    res, doc = run_json("convert", png, "--to", "jpeg", "--json")
    assert doc["items"][0]["status"] == "skipped_existing"


@need
def test_json_keeps_human_text_off_stdout(png):
    res = runner.invoke(app, ["convert", str(png), "--to", "jpeg", "--json"])
    assert res.stdout.lstrip().startswith("{") and "✓" not in res.stdout
    assert "✓" in res.stderr or "written" in res.stderr


@need
def test_convert_json_failure_has_status_and_exit_1(tmp_path):
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not an image")
    res, doc = run_json("convert", bad, "--to", "jpeg", "--json")
    check_envelope(doc, "convert", exit_code=1)
    assert doc["items"][0]["status"] == "failed" and res.exit_code == 1


@need
def test_convert_json_note_is_a_warning(tmp_path):
    gif = tmp_path / "anim.gif"
    subprocess.run(["magick", "-delay", "10", "-size", "20x20", "xc:red", "xc:blue", str(gif)], check=True)
    res, doc = run_json("convert", gif, "--to", "png", "--json")
    check_envelope(doc, "convert")
    assert any("frame" in w for w in doc["warnings"])


@need
def test_dry_run_json_lists_the_plan_and_writes_nothing(png, tmp_path):
    res, doc = run_json("convert", png, "--to", "jpeg,webp", "--dry-run", "--json")
    check_envelope(doc, "convert")
    assert [i["status"] for i in doc["items"]] == ["planned", "planned"]
    assert not (tmp_path / "a.jpg").exists()


@need
def test_export_json_items_and_cmyk_report(svg, tmp_path):
    res, doc = run_json("export", svg, "--formats", "png,pdf", "--profiles", "rgb,cmyk", "--sizes", "64",
                        "--cmyk-mode", "clean", "--out", tmp_path / "o", "--json")
    check_envelope(doc, "export")
    formats = {(i["format"], i["colorspace"]) for i in doc["items"]}
    assert ("png", "rgb") in formats and ("pdf", "cmyk") in formats
    assert all(i["status"] == "written" for i in doc["items"])
    assert doc["cmyk"]["mode"] == "clean" and doc["cmyk"]["colours"][0]["rgb"].startswith("#")
    assert any("png" in w and "skipped" in w for w in doc["warnings"])        # PNG has no CMYK


@need
def test_export_dry_run_json(svg, tmp_path):
    res, doc = run_json("export", svg, "--formats", "png", "--sizes", "16,32", "--out", tmp_path / "o", "--dry-run", "--json")
    check_envelope(doc, "export")
    assert [i["size"] for i in doc["items"]] == ["16", "32"] and not (tmp_path / "o").exists()


@need
def test_export_json_error_is_structured_not_a_traceback(tmp_path):
    bad = tmp_path / "bad.svg"
    bad.write_text("<svg xmlns='http://www.w3.org/2000/svg'><rect")
    res, doc = run_json("export", bad, "--formats", "png", "--sizes", "16", "--out", tmp_path / "o", "--json")
    check_envelope(doc, "export", exit_code=1)
    assert doc["errors"] and "Traceback" not in res.stdout


@need
def test_missing_tool_is_exit_4_with_install_hints(png, tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text('[tools]\nmagick = "magick-NOT-INSTALLED"\n')
    res, doc = run_json("convert", png, "--to", "jpeg", "--config", cfg, "--json")
    check_envelope(doc, "convert", exit_code=4)
    assert res.exit_code == 4
    assert doc["missing_tools"][0]["tool"] == "magick" and "brew install" in doc["missing_tools"][0]["install_hint"]


@need
def test_metadata_strip_and_randomize_json(png):
    res, doc = run_json("metadata", "strip", png, "--json")
    check_envelope(doc, "metadata-strip")
    assert doc["items"][0]["status"] == "written"
    res, doc = run_json("metadata", "randomize", png, "--json")
    check_envelope(doc, "metadata-randomize")


@need
def test_watermark_json(png, tmp_path):
    logo = tmp_path / "logo.png"
    subprocess.run(["magick", "-size", "10x10", "xc:blue", f"PNG24:{logo}"], check=True)
    res, doc = run_json("watermark", png, "--logo", logo, "--json")
    check_envelope(doc, "watermark")
    assert doc["items"][0]["output"].endswith("a_watermarked.png")


@need
def test_compress_json_reports_missing_codec_as_exit_4(png, tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text('[tools]\noxipng = "oxipng-NOT-INSTALLED"\n')
    res, doc = run_json("compress", png, "--codec", "oxipng", "--config", cfg, "--json")
    check_envelope(doc, "compress", exit_code=4)


def test_enhance_json_includes_sizes_and_backend(png, tmp_path):
    res, doc = run_json("enhance", "run", png, "--scale", "1", "--backend", "classical", "--json")
    check_envelope(doc, "enhance")
    item = doc["items"][0]
    assert item["status"] == "written" and item["backend"] == "classical" and item["output_size"] == [40, 30]


def test_doctor_json_lists_every_tool():
    res, doc = run_json("doctor", "--json")
    assert doc["command"] == "doctor" and doc["schema_version"] == 1
    names = {i["tool"] for i in doc["items"]}
    assert {"magick", "gs", "exiftool", "cutout_tool"} <= names
    assert all("found" in i and ("install_hint" in i) for i in doc["items"])


def test_without_json_nothing_changes(png):
    res = runner.invoke(app, ["doctor"])
    assert not res.stdout.lstrip().startswith("{")
