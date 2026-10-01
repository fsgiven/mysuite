from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from textual.widgets import Input, RichLog, Select
from typer.testing import CliRunner

from mysuite.cli import app
from mysuite.tui.app import MysuiteApp
from mysuite.tui.registry import TOOL_REGISTRY
from mysuite.utils.paths import show_path, tilde

runner = CliRunner()
# Before paths were escaped these either crashed Rich (a folder "x [" holding
# "bold].png" makes the path "x [/bold].png" -> MarkupError), restyled the log, or
# injected a clickable link. A "/" can't be in one file name, hence the nesting.
HOSTILE = ["photo [red].png", "a [link=x]b.png", "x [/bold].png"]


def test_tilde_shortens_only_paths_under_home(monkeypatch):
    monkeypatch.setenv("HOME", "/Users/demo")
    assert tilde("/Users/demo/Pictures/a.png") == "~/Pictures/a.png"
    assert tilde("/Users/demo") == "~"
    assert tilde("/Users/demo2/a.png") == "/Users/demo2/a.png"  # not a prefix match on a sibling
    assert tilde("/tmp/a.png") == "/tmp/a.png"


def test_show_path_escapes_markup():
    assert "[/bold]" not in show_path("/tmp/x [/bold].png").replace("\\[/bold]", "")


@pytest.fixture
def hostile_dir(tmp_path):
    arr = np.random.default_rng(0).integers(0, 255, (24, 32, 3), dtype=np.uint8)
    for name in HOSTILE:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(arr).save(target)
    return tmp_path


def test_cli_survives_hostile_filenames(hostile_dir):
    result = runner.invoke(app, ["enhance", "run", str(hostile_dir), "--recursive", "--backend", "classical", "--scale", "1"])
    assert result.exit_code == 0, result.stdout
    assert result.exception is None
    assert "3 written" in result.stdout


def test_cli_dry_run_and_errors_survive_hostile_filenames(hostile_dir):
    assert runner.invoke(app, ["enhance", "run", str(hostile_dir), "--recursive", "--dry-run"]).exit_code == 0
    assert runner.invoke(app, ["compress", str(hostile_dir), "--recursive", "--codec", "webp", "--dry-run"]).exit_code == 0
    (hostile_dir / "broken [").mkdir()
    bad = hostile_dir / "broken [" / "bold].png"
    bad.write_bytes(b"not an image")
    result = runner.invoke(app, ["enhance", "run", str(bad)])
    assert result.exit_code == 1 and result.exception is None or isinstance(result.exception, SystemExit)


async def test_tui_log_survives_hostile_filenames_and_uses_tilde(hostile_dir, monkeypatch):
    monkeypatch.setenv("HOME", str(hostile_dir))
    index = next(i for i, s in enumerate(TOOL_REGISTRY, start=1) if s.key == "enhance")
    async with MysuiteApp().run_test() as pilot:
        await pilot.pause()
        await pilot.press(str(index))
        await pilot.pause()
        screen = pilot.app.screen
        screen.query_one("#input-files", Input).value = str(hostile_dir)
        screen.query_one("#backend", Select).value = "classical"
        screen.query_one("#scale", Input).value = "1"
        screen.query_one("#recursive").value = True
        screen.action_run()
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()
        text = "\n".join(str(line) for line in screen.query_one("#run-log", RichLog).lines)
        assert "3 written" in str(screen.query_one("#run-summary").render())
        assert "~/" in text and str(hostile_dir) not in text
        assert "photo [red]_enhanced.png" in text and "x [/bold]_enhanced.png" in text  # literal, not parsed as markup
