"""WS-E doctor/install: required vs optional tools, what each tool unlocks, exit 0 when only optional ones are
missing, and docs that match the commands."""
from __future__ import annotations

import json
import shutil

import pytest

from mysuite import components, doctor
from mysuite.cli import app
from tests.helpers import runner


def _fake_which(missing: set[str]):
    real = shutil.which

    def which(name, *a, **kw):
        return None if any(m in str(name) for m in missing) else (real(name, *a, **kw) or f"/usr/bin/{name}")
    return which


def test_every_tool_is_required_or_optional_and_unlocks_something():
    for spec in doctor.TOOL_SPECS:
        assert isinstance(spec.required, bool)
        assert doctor.unlocks(spec.attr), f"{spec.attr} unlocks nothing?"
    required = {s.attr for s in doctor.TOOL_SPECS if s.required}
    assert required == {"rsvg_convert", "gs", "magick", "exiftool"}
    assert "ocr" in doctor.unlocks("vision_tool") and "qr read" in doctor.unlocks("vision_tool")
    assert "compress --codec mozjpeg" in doctor.unlocks("cjpeg")


def test_only_optional_tools_missing_is_exit_0(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", _fake_which({"mysuite-vision", "c2patool", "pdftocairo"}))
    res = runner.invoke(app, ["doctor", "--json"])
    doc = json.loads(res.stdout)
    assert res.exit_code == 0 and doc["ok"] is True
    by = {i["tool"]: i for i in doc["items"]}
    assert by["vision_tool"]["found"] is False and by["vision_tool"]["required"] is False
    assert "ocr" in by["vision_tool"]["unlocks"]
    assert by["magick"]["required"] is True and by["magick"]["found"] is True
    assert doc["missing_required"] == [] and set(doc["missing_optional"]) == {"vision_tool", "c2patool", "pdftocairo"}


def test_a_missing_required_tool_is_exit_1(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", _fake_which({"magick"}))
    res = runner.invoke(app, ["doctor", "--json"])
    doc = json.loads(res.stdout)
    assert res.exit_code == 1 and doc["missing_required"] == ["magick"]


def test_human_doctor_separates_required_and_optional(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", _fake_which({"mysuite-vision"}))
    res = runner.invoke(app, ["doctor"], env={"COLUMNS": "160"})
    out = res.output
    assert "required" in out and "optional" in out
    assert "ocr" in out                                      # what installing it would unlock
    assert res.exit_code == 0


def test_install_docs_match_the_command():
    doc = components.__doc__
    assert "mysuite install list" not in doc and "install remove" not in doc
    assert "mysuite install NAME --remove" in doc
    assert components.COMPONENTS["cutout"].needs.startswith("macOS 14")    # Vision's subject masks are macOS 14+
    assert components.COMPONENTS["vision"].needs.startswith("macOS 13")


def test_install_reports_one_item_per_component(monkeypatch, tmp_path):
    monkeypatch.setenv("MYSUITE_HOME", str(tmp_path))
    c = components.COMPONENTS["vision"]
    monkeypatch.setattr(components, "supported", lambda comp: True)
    monkeypatch.setattr(c, "install", lambda log: None)
    monkeypatch.setattr(c, "is_installed", lambda: False)
    res = runner.invoke(app, ["install", "vision", "--yes", "--json"])
    doc = json.loads(res.stdout)
    assert res.exit_code == 0, doc
    assert [i["status"] for i in doc["items"]] == ["installed"]
