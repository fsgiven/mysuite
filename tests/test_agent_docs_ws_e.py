"""llms.txt and the skill list every tool (not only the first seven), and AGENTS.md covers the WS-E features."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from mysuite import schema

ROOT = Path(__file__).resolve().parent.parent
TOP = sorted({name.split()[0] for name in schema.build()["commands"]} - {"tui", "preset"})


@pytest.mark.parametrize("doc", ["llms.txt", "skills/mysuite/SKILL.md"])
def test_every_top_level_command_is_listed(doc):
    text = (ROOT / doc).read_text()
    missing = [c for c in TOP if not re.search(rf"`{re.escape(c)}\b", text)]
    assert not missing, f"{doc} does not mention: {missing}"


def test_experimental_tools_are_marked_as_such():
    for doc in ("llms.txt", "skills/mysuite/SKILL.md"):
        text = (ROOT / doc).read_text()
        for tool in ("shield", "relight"):
            line = next(l for l in text.splitlines() if f"`{tool}" in l)
            assert "xperimental" in line, (doc, line)


def test_agents_md_covers_pipeline_validate_slim_and_statuses():
    text = (ROOT / "AGENTS.md").read_text()
    assert "mysuite pipeline validate" in text and "keep_intermediate" in text
    assert "--slim" in text
    assert "not_smaller" in text and "mysuite schema" in text
    assert "choices" in text
