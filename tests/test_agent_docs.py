"""The agent docs are tested so they cannot rot: examples are executed, the reference is regenerated."""
from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from mysuite import schema
from mysuite.cli import app
from tests.helpers import SIMPLE_SVG, runner

ROOT = Path(__file__).resolve().parent.parent
need = pytest.mark.skipif(not all(shutil.which(t) for t in ("rsvg-convert", "gs", "magick", "exiftool", "c2patool", "oxipng")), reason="needs tools")


def doctest_lines() -> list[str]:
    text = (ROOT / "AGENTS.md").read_text()
    lines: list[str] = []
    for block in re.findall(r"```bash doctest\n(.*?)```", text, re.S):
        lines += [ln.strip() for ln in block.splitlines() if ln.strip() and not ln.startswith("#")]
    return lines


def test_there_are_examples_to_run():
    assert len(doctest_lines()) >= 6


@need
@pytest.mark.parametrize("line", doctest_lines())
def test_every_agents_md_example_runs(line, tmp_path, monkeypatch):
    (tmp_path / "logo.svg").write_text(SIMPLE_SVG)
    (tmp_path / "tokens.css").write_text(':root{--brand-red:#dd0000;--brand-blue:#0057b8}[data-theme="dark"]{--brand-red:#ff6b6b;--brand-blue:#6bb0ff}')
    subprocess.run(["magick", "-size", "40x30", "xc:#dd0000", f"PNG24:{tmp_path / 'pic.png'}"], check=True)
    subprocess.run(["magick", "-size", "40x30", "xc:#336699", str(tmp_path / "photo.jpg")], check=True)
    toml_block = re.search(r"```toml\n(.*?)```", (ROOT / "AGENTS.md").read_text(), re.S).group(1)
    (tmp_path / "release.toml").write_text(toml_block)               # the pipeline example from the doc
    monkeypatch.chdir(tmp_path)
    argv = shlex.split(line)
    assert argv[0] == "mysuite", line
    res = runner.invoke(app, argv[1:])
    doc = json.loads(res.stdout)                                  # every example prints one JSON document
    assert doc["schema_version"] == 1, line
    if argv[1] != "doctor":                                       # doctor is non-zero when an optional tool is missing
        assert res.exit_code == 0, (line, res.stdout, res.stderr)


def test_commands_reference_is_up_to_date():
    committed = (ROOT / "docs" / "agents" / "COMMANDS.md").read_text()
    assert committed == schema.markdown(), "run: mysuite schema --markdown > docs/agents/COMMANDS.md"


def test_agents_md_mentions_every_top_level_command():
    text = (ROOT / "AGENTS.md").read_text()
    top = {name.split()[0] for name in schema.build()["commands"]}
    # preset/enhance history etc. are human conveniences; everything else must be documented for agents
    missing = sorted(c for c in top if c not in {"preset", "schema"} and f"mysuite {c}" not in text)
    assert not missing, f"AGENTS.md does not mention: {missing}"


def test_llms_txt_links_exist():
    for target in re.findall(r"\]\(([^)]+)\)", (ROOT / "llms.txt").read_text()):
        assert (ROOT / target).exists(), target


def test_skill_has_frontmatter():
    text = (ROOT / "skills" / "mysuite" / "SKILL.md").read_text()
    assert text.startswith("---\nname: mysuite\ndescription: ")


def test_every_json_capable_command_is_in_the_schema():
    commands = schema.build()["commands"]
    for name in ("export", "convert", "cutout", "watermark", "compress", "inspect", "doctor", "metadata strip",
                 "metadata credit", "metadata randomize", "enhance run"):
        flags = {f for o in commands[name]["options"] for f in o["flags"]}
        assert "--json" in flags, name
