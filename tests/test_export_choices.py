"""WS-A item 6: export options with a fixed set of values declare it, so the schema (and agents) can see it."""
from __future__ import annotations

import re

from mysuite import schema
from mysuite.export.units import SIZE_BY, VALID_UNITS
from tests.helpers import cli, write_svg


_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def plain(text: str) -> str:
    """CLI output as a person reads it: no colour codes (CI forces colour), no Rich box, wrapped lines joined."""
    text = _ANSI.sub("", text)
    text = re.sub(r"[│╭╮╰╯─]", " ", text)
    return " ".join(text.split())


def _options() -> dict[str, dict]:
    return {o["name"]: o for o in schema.build()["commands"]["export"]["options"]}


def test_fixed_value_options_declare_their_choices():
    opts = _options()
    assert opts["size_by"].get("choices") == list(SIZE_BY)
    assert sorted(opts["unit"].get("choices")) == sorted(VALID_UNITS)
    assert opts["theme"].get("choices") == ["light", "dark"]


def test_choices_are_case_insensitive_and_still_rejected_when_wrong(tmp_path):
    svg = write_svg(tmp_path / "a.svg", '<rect width="200" height="100"/>')
    ok = cli("export", svg, "--formats", "png", "--sizes", "16", "--size-by", "HEIGHT", "--unit", "PX", "--out", tmp_path / "o", "--dry-run", "-q")
    assert ok.exit_code == 0, ok.stdout
    bad = cli("export", svg, "--formats", "png", "--theme", "sepia", "--out", tmp_path / "o", "--dry-run", "-q")
    assert bad.exit_code == 2
    shown = plain(bad.output)
    assert "--theme" in shown and "'light'" in shown and "'dark'" in shown      # the user sees what is allowed
