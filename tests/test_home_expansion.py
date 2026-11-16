"""`~` in an output folder means the home folder, also when no shell expanded it.

The dashboard, the MCP server and pipelines start `python -m mysuite …` without a shell, so a
typed `~/demo/exports` used to reach the CLI literally and land in `./~/demo/exports`.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from textual.widgets import Input

from mysuite.pipeline.engine import _child_env
from mysuite.tui.app import MysuiteApp
from mysuite.utils.paths import expand_home, expand_home_args
from tests.helpers import SIMPLE_SVG

need_tools = pytest.mark.skipif(any(shutil.which(t) is None for t in ("rsvg-convert", "gs", "magick")), reason="needs tools")


@pytest.fixture
def home(tmp_path, monkeypatch):
    folder = tmp_path / "home"
    folder.mkdir()
    monkeypatch.setenv("HOME", str(folder))
    assert Path.home() == folder
    return folder


@pytest.fixture
def work(tmp_path, monkeypatch):
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "logo.svg").write_text(SIMPLE_SVG)
    monkeypatch.chdir(folder)
    return folder


def test_expand_home_mirrors_the_shell(home):
    assert expand_home("~") == str(home)
    assert expand_home("~/demo/exports") == str(home / "demo" / "exports")
    assert expand_home("--out=~/demo") == f"--out={home / 'demo'}"
    # left alone: not a leading home reference
    for word in ("a~/b", "./~/x", "#dd0000=~", "--text", "~nosuchuser-mysuite/x", "-", ""):
        assert expand_home(word) == word
    assert expand_home_args(["export", "--out", "~/x"]) == ["export", "--out", str(home / "x")]


def test_cli_without_a_shell_writes_under_home(home, work):
    """What the TUI, MCP and pipelines do: argv straight to `python -m mysuite`, no shell."""
    proc = subprocess.run(
        [sys.executable, "-m", "mysuite", "export", "logo.svg", "--formats", "png", "--sizes", "16",
         "--out", "~/demo/exports", "--dry-run", "--json"],
        capture_output=True, text=True, cwd=work, env={**_child_env(), "HOME": str(home)},
    )
    assert proc.returncode == 0, proc.stderr
    outputs = [Path(i["output"]) for i in json.loads(proc.stdout)["items"]]
    assert outputs and all(home / "demo" / "exports" in p.parents for p in outputs), outputs
    assert not (work / "~").exists()


async def _export_screen(pilot, svg: Path, out_dir: str):
    await pilot.pause()
    pilot.app.screen._open_tool("export")
    await pilot.pause()
    sc = pilot.app.screen
    sc.query_one("#input-svg", Input).value = str(svg)
    sc.query_one("#sizes", Input).value = "16"
    sc.query_one("#out-dir", Input).value = out_dir
    await pilot.pause()
    return sc


async def test_tui_export_command_and_plan_agree_on_a_tilde_folder(home, work):
    async with MysuiteApp().run_test() as pilot:
        sc = await _export_screen(pilot, work / "logo.svg", "~/demo/exports")
        argv = sc.argv()
        target = home / "demo" / "exports"
        assert argv[argv.index("--out") + 1] == str(target)
        assert sc.output_dir() == target.resolve()
        assert "'~/" not in " ".join(argv) and "~/demo" not in argv


@pytest.mark.slow
@need_tools
async def test_tui_export_run_writes_under_home_not_a_literal_tilde(home, work):
    async with MysuiteApp().run_test() as pilot:
        sc = await _export_screen(pilot, work / "logo.svg", "~/demo/exports")
        sc.action_run()
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()
    assert sorted(p.name for p in (home / "demo" / "exports").rglob("*.png")) == ["logo_16.png"]
    assert not (work / "~").exists() and not (Path(os.getcwd()) / "~").exists()
