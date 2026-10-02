from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest
from PIL import Image

from mysuite import components as C
from mysuite.cli import app
from mysuite.shield import core as SC
from tests.helpers import runner


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MYSUITE_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def run(*args, **kw):
    res = runner.invoke(app, [str(a) for a in args] + ["--json"], **kw)
    return res, json.loads(res.stdout)


# ------------------------------------------------------------------------------ layout
def test_everything_lives_under_one_removable_folder(home):
    assert C.root() == home and C.bin_dir() == home / "bin" and C.env_dir("shield") == home / "envs" / "shield"
    assert C.models_dir() == home / "models"


def test_default_home_is_the_user_cache(monkeypatch):
    monkeypatch.delenv("MYSUITE_HOME", raising=False)
    assert C.root() == Path.home() / ".cache" / "mysuite"


def test_activate_path_is_idempotent_and_creates_nothing(home, monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin")
    C.activate_path()
    C.activate_path()
    parts = __import__("os").environ["PATH"].split(__import__("os").pathsep)
    assert parts.count(str(C.bin_dir())) == 1 and parts[0] == str(C.bin_dir()) and not home.exists()


def test_a_helper_in_the_bin_folder_is_found_like_any_tool(home, monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin")
    C.bin_dir().mkdir(parents=True)
    helper = C.bin_dir() / "mysuite-vision"
    helper.write_text("#!/bin/sh\necho hi\n")
    helper.chmod(0o755)
    assert shutil.which("mysuite-vision") is None
    C.activate_path()
    assert shutil.which("mysuite-vision") == str(helper)
    assert C.get("vision").is_installed()


def test_unknown_component():
    with pytest.raises(C.ComponentError, match="available"):
        C.get("nope")


# ------------------------------------------------------------------------------ listing (no network, no side effects)
def test_list_never_runs_anything_and_reports_state(home, monkeypatch):
    monkeypatch.setattr(C, "_run", lambda *a, **k: pytest.fail("listing must not run commands"))
    res, doc = run("install")
    assert res.exit_code == 0 and {i["name"] for i in doc["items"]} == {"tools", "cutout", "vision", "shield"}
    shield = next(i for i in doc["items"] if i["name"] == "shield")
    assert shield["installed"] is False and "GB" in shield["size"] and "internet" in shield["needs"] and not home.exists()


def test_install_needs_confirmation_when_there_is_no_terminal(home, monkeypatch):
    monkeypatch.setattr(C, "_run", lambda *a, **k: pytest.fail("nothing may run without --yes"))
    res, doc = run("install", "shield")
    assert res.exit_code == 1 and "--yes" in doc["errors"][0] and not home.exists()


def test_dry_run_shows_the_plan_and_changes_nothing(home, monkeypatch):
    monkeypatch.setattr(C, "_run", lambda *a, **k: pytest.fail("dry run must not run commands"))
    res, doc = run("install", "shield", "--dry-run")
    item = doc["items"][0]
    assert res.exit_code == 0 and item["status"] == "planned" and any("pip install" in s for s in item["steps"]) and not home.exists()


def test_unknown_and_unsupported_components(home, monkeypatch):
    assert run("install", "nope")[0].exit_code == 1
    monkeypatch.setattr(C.platform, "system", lambda: "Linux")
    res, doc = run("install", "vision", "--yes")
    assert res.exit_code == 1 and "not available" in doc["errors"][0]


# ------------------------------------------------------------------------------ the shield install steps
def fake_runner(calls):
    def fake(cmd, log, cwd=None):
        calls.append(cmd)
        if cmd[1:3] == ["-m", "venv"]:
            Path(cmd[3], "bin").mkdir(parents=True)
            Path(cmd[3], "bin", "python").write_text("")
    return fake


def test_shield_install_creates_a_private_env_installs_pinned_packages_and_fetches_the_pinned_weights(home, monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(C, "_run", fake_runner(calls))
    res, doc = run("install", "shield", "--yes")
    assert res.exit_code == 0 and doc["items"][-1]["status"] == "installed"
    assert calls[0][:3] == [sys.executable, "-m", "venv"] and calls[0][3] == str(C.env_dir("shield"))
    pip = calls[1]
    assert pip[0] == str(C.env_python("shield")) and pip[1:3] == ["-m", "pip"] and "torch>=2.2" in pip
    fetch = calls[2][-1]
    assert C.SHIELD_VAE_REVISION in fetch and "cache_dir" in fetch and str(C.models_dir()) in fetch and "allow_patterns" in fetch
    assert C.get("shield").is_installed()
    again = run("install", "shield", "--yes")[1]
    assert again["items"][0]["status"] == "already_installed" and len(calls) == 3                  # nothing runs twice


def test_a_failed_install_leaves_nothing_half_installed(home, monkeypatch):
    def failing(cmd, log, cwd=None):
        if cmd[1:3] == ["-m", "venv"]:
            Path(cmd[3], "bin").mkdir(parents=True)
            Path(cmd[3], "bin", "python").write_text("")
        elif "pip" in cmd:
            raise C.ComponentError("pip failed: no internet")
    monkeypatch.setattr(C, "_run", failing)
    res, doc = run("install", "shield", "--yes")
    assert res.exit_code == 1 and "no internet" in doc["errors"][0]
    assert not C.env_dir("shield").exists() and not C.get("shield").is_installed()


def test_remove_deletes_exactly_what_was_installed(home, monkeypatch):
    monkeypatch.setattr(C, "_run", fake_runner([]))
    run("install", "shield", "--yes")
    (C.models_dir() / "models--stabilityai--sd-vae-ft-mse").mkdir(parents=True)
    keep = C.models_dir() / "something-else"
    keep.mkdir()
    res, doc = run("install", "shield", "--remove")
    assert res.exit_code == 0 and doc["items"][0]["status"] == "removed"
    assert not C.env_dir("shield").exists() and not (C.models_dir() / "models--stabilityai--sd-vae-ft-mse").exists() and keep.exists()
    assert run("install", "shield", "--remove")[1]["items"][0]["status"] == "not_installed"


def test_native_install_explains_a_missing_compiler_and_builds_with_swiftc(home, monkeypatch):
    monkeypatch.setattr(C.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(C.shutil, "which", lambda name: None)
    res, doc = run("install", "vision", "--yes")
    assert res.exit_code == 1 and "xcode-select --install" in doc["errors"][0]
    calls: list[list[str]] = []

    def fake(cmd, log, cwd=None):
        calls.append(cmd)
        Path(cmd[-1]).write_text("binary")

    monkeypatch.setattr(C.shutil, "which", lambda name: "/usr/bin/swiftc" if name == "swiftc" else None)
    monkeypatch.setattr(C, "_run", fake)
    res, doc = run("install", "vision", "--yes")
    assert res.exit_code == 0 and calls[0][0] == "swiftc" and calls[0][-1] == str(C.bin_dir() / "mysuite-vision")
    assert (C.bin_dir() / "mysuite-vision").exists()
    assert run("install", "vision", "--remove")[0].exit_code == 0 and not (C.bin_dir() / "mysuite-vision").exists()


def test_the_sources_of_the_helpers_ship_inside_the_package():
    for name in ("cutout", "vision"):
        assert (C.PACKAGE / "native" / name / "main.swift").exists()


# ------------------------------------------------------------------------------ shield command
@pytest.fixture
def img(tmp_path):
    p = tmp_path / "a.png"
    Image.new("RGB", (64, 48), (120, 80, 60)).save(p)
    return p


def test_shield_without_the_component_is_exit_4_with_the_install_command(img, home):
    res, doc = run("shield", img)
    assert res.exit_code == 4 and doc["missing_components"][0]["install"] == "mysuite install shield --yes"
    assert not (img.parent / "a_shielded.png").exists()


def test_shield_dry_run_works_without_the_component_and_estimates_time(img, home):
    res, doc = run("shield", img, "--dry-run", "--strength", "strong")
    item = doc["items"][0]
    assert res.exit_code == 0 and item["status"] == "planned" and item["estimated_seconds"] == 100 and item["width"] == 64


@pytest.mark.parametrize("args", [["--strength", "max"], ["--steps", "2"], ["--epsilon", "99"], ["--device", "tpu"]])
def test_shield_validation(img, home, args, monkeypatch):
    monkeypatch.setattr(C.get("shield"), "is_installed", lambda: True)
    res, doc = run("shield", img, *args)
    assert res.exit_code == 1 and not (img.parent / "a_shielded.png").exists()


def test_estimate_scales_with_tiles():
    assert SC.estimate_seconds(512, 512, 60) == 60 and SC.estimate_seconds(1024, 512, 60) == 120 and SC.estimate_seconds(1100, 600, 10) == 60


def test_shield_skips_existing_and_refuses_vectors_and_sandbox(img, home, monkeypatch, tmp_path):
    monkeypatch.setattr(C.get("shield"), "is_installed", lambda: True)
    (img.parent / "a_shielded.png").write_bytes(b"keep")
    res, doc = run("shield", img)
    assert doc["items"][0]["status"] == "skipped_existing" and (img.parent / "a_shielded.png").read_bytes() == b"keep"
    svg = tmp_path / "x.svg"
    svg.write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    assert run("shield", svg)[0].exit_code == 1
    jail = tmp_path / "jail"
    jail.mkdir()
    assert runner.invoke(app, ["--allow", str(jail), "shield", str(img), "--json"]).exit_code == 3


def test_worker_failures_are_reported_not_raised(img, home, monkeypatch):
    monkeypatch.setattr(C.get("shield"), "is_installed", lambda: True)
    monkeypatch.setattr(C, "env_python", lambda name: Path(sys.executable))
    monkeypatch.setattr(SC, "WORKER", Path(__file__))                  # "worker" that is not a worker: exits non-zero
    res, doc = run("shield", img, "--steps", "5")
    assert res.exit_code == 1 and doc["items"][0]["status"] == "failed" and not (img.parent / "a_shielded.png").exists()


# --------------------------------------------------------------- the real thing (needs `mysuite install shield`)
@pytest.mark.skipif(not (Path.home() / ".cache" / "mysuite" / "envs" / "shield" / ".mysuite-ready").exists(), reason="shield component not installed")
def test_real_shield_changes_pixels_within_the_budget_and_keeps_size_and_alpha(tmp_path):
    import numpy as np

    rgba = Image.new("RGBA", (72, 50), (90, 140, 200, 255))
    rgba.paste((200, 30, 30, 255), (10, 10, 40, 40))
    rgba.putpixel((0, 0), (0, 0, 0, 0))
    src = tmp_path / "t.png"
    rgba.save(src)
    res = runner.invoke(app, ["shield", str(src), "--steps", "6", "--epsilon", "10", "--json"])
    doc = json.loads(res.stdout)
    assert res.exit_code == 0, doc
    out = Image.open(tmp_path / "t_shielded.png")
    assert out.size == (72, 50) and out.mode == "RGBA" and out.getpixel((0, 0))[3] == 0
    a, b = np.asarray(rgba.convert("RGB"), int), np.asarray(out.convert("RGB"), int)
    assert 0 < np.abs(a - b).max() <= 10 and doc["items"][0]["metrics"]["max_pixel_change"] <= 10
    assert (tmp_path / "t.png").read_bytes() == src.read_bytes()


def test_tools_component_installs_only_what_is_missing_via_brew(home, monkeypatch):
    present = {"gs", "magick", "brew"}
    monkeypatch.setattr(C.shutil, "which", lambda name: f"/x/{name}" if name in present else None)
    assert "ghostscript" not in C._missing_formulae() and "librsvg" in C._missing_formulae()
    calls: list[list[str]] = []
    monkeypatch.setattr(C, "_run", lambda cmd, log, cwd=None: calls.append(cmd))
    res, doc = run("install", "tools", "--dry-run")
    assert doc["items"][0]["steps"][0].startswith("brew install ") and "librsvg" in doc["items"][0]["steps"][0] and "ghostscript" not in doc["items"][0]["steps"][0]
    res, doc = run("install", "tools", "--yes")
    assert res.exit_code == 0 and calls[0][:2] == ["brew", "install"] and "librsvg" in calls[0] and "imagemagick" not in calls[0]
    assert run("install", "tools", "--remove")[0].exit_code == 1                     # shared with the rest of the system
    present.discard("brew")
    assert run("install", "tools", "--yes")[0].exit_code == 1
