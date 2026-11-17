"""WS-D decisions 1 and 2: randomize is behind --advanced; shield stays, experimental, honest about what it does not stop."""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from mysuite.cli import app
from tests.helpers import runner

need = pytest.mark.skipif(shutil.which("exiftool") is None or shutil.which("magick") is None, reason="needs exiftool+magick")


def test_randomize_is_not_in_the_metadata_help():
    out = runner.invoke(app, ["metadata", "--help"]).output
    assert "randomize" not in out.lower() and "decoy" not in out.lower()
    assert "protect" in out


@need
def test_randomize_without_advanced_is_refused(tmp_path):
    src = tmp_path / "p.jpg"
    subprocess.run(["magick", "-size", "40x30", "xc:red", str(src)], check=True)
    res = runner.invoke(app, ["metadata", "randomize", str(src), "--json"])
    assert res.exit_code == 2
    doc = json.loads(res.stdout)
    assert "--advanced" in doc["errors"][0] and "false provenance" in doc["errors"][0]
    assert not (tmp_path / "p_randomized.jpg").exists()


@need
def test_randomize_with_advanced_runs(tmp_path):
    src = tmp_path / "p.jpg"
    subprocess.run(["magick", "-size", "40x30", "xc:red", str(src)], check=True)
    res = runner.invoke(app, ["metadata", "randomize", str(src), "--advanced", "--json"])
    assert res.exit_code == 0, res.output
    assert (tmp_path / "p_randomized.jpg").exists()


def test_shield_help_says_what_it_does_not_stop():
    out = " ".join(runner.invoke(app, ["shield", "--help"]).output.split())
    assert "EXPERIMENTAL" in out and "ChatGPT" in out and "resiz" in out.lower() and "training" in out.lower()


def test_the_protect_card_has_no_shield_and_no_randomize_anywhere_on_the_dashboard():
    from mysuite.tui.registry import TOOL_REGISTRY as TOOLS
    from mysuite.tui.screens.metadata_screen import MODES

    protect = next(t for t in TOOLS if t.key == "protect")
    assert all(cmd != ("shield",) for _, cmd in protect.commands)
    assert ("metadata", "protect") in [cmd for _, cmd in protect.commands]
    assert "shield" not in protect.description.lower()
    assert "randomize" not in [value for _, value in MODES]
    meta = next(t for t in TOOLS if t.key == "metadata")
    assert "fake" not in meta.description.lower() and "randomize" not in meta.help.lower()
