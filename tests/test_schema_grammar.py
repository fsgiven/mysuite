"""The schema is a grammar a small local model can't wander outside of: every command says what values its
options take (choices), shows runnable examples, and says whether it writes files and can dry-run."""
from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest

from mysuite import schema

ROOT = Path(__file__).resolve().parent.parent
DATA = schema.build()
COMMANDS = DATA["commands"]


def _flags(cmd: dict) -> dict[str, dict]:
    out = {}
    for o in cmd["options"] + DATA["global_options"]:
        for f in o["flags"]:
            out[f] = o
    return out


def test_every_command_has_flags_and_examples():
    for name, cmd in COMMANDS.items():
        assert isinstance(cmd["writes_files"], bool), name
        assert isinstance(cmd["has_dry_run"], bool), name
        assert cmd["examples"], f"{name}: add an example to schema.EXAMPLES"


def test_has_dry_run_matches_the_options():
    for name, cmd in COMMANDS.items():
        assert cmd["has_dry_run"] == any("--dry-run" in o["flags"] for o in cmd["options"]), name


@pytest.mark.parametrize("name", sorted(COMMANDS))
def test_examples_only_use_real_flags_and_choices(name):
    cmd = COMMANDS[name]
    flags = _flags(cmd)
    for example in cmd["examples"]:
        argv = shlex.split(example)
        assert argv[0] == "mysuite", example
        # global options may come first: mysuite --allow DIR export …
        words = [w for w in argv[1:] if not w.startswith("-")]
        assert " ".join(words).startswith(name) or name in example, example
        for i, word in enumerate(argv):
            if not word.startswith("--") or word == "--":
                continue
            flag = word.split("=", 1)[0]
            assert flag in flags, f"{example}: {flag} is not an option of `{name}`"
            opt = flags[flag]
            if opt.get("choices") and opt["type"] != "boolean":
                value = word.split("=", 1)[1] if "=" in word else argv[i + 1]
                values = value.split(",") if opt.get("comma_separated") else [value]
                for v in values:
                    assert v in opt["choices"], f"{example}: {v!r} is not one of {opt['choices']}"


def test_read_only_commands_say_so():
    for name in ("inspect", "dupes", "contrast", "doctor", "schema", "pdf info", "tokens list", "mark detect", "kit list"):
        assert COMMANDS[name]["writes_files"] is False, name
    for name in ("export", "convert", "pdf merge", "qr make", "pipeline run", "sheet"):
        assert COMMANDS[name]["writes_files"] is True, name
    # read-only by default, but an option makes it write
    assert COMMANDS["inspect"]["writes_files_with"] == ["--thumb", "--sheet"]
    assert COMMANDS["diff"]["writes_files_with"] == ["--out"]


def test_choices_from_my_own_options():
    qr = {o["name"]: o for o in COMMANDS["qr make"]["options"]}
    assert qr["error"]["choices"] == ["l", "m", "q", "h"]
    install = COMMANDS["install"]["arguments"][0]
    assert set(install["choices"]) >= {"tools", "vision", "cutout", "shield"}


def test_result_status_enum_lists_every_status_the_code_emits():
    """Scan the source for status literals, so a new status can't go unlisted."""
    enum = set(DATA["result"]["properties"]["items"]["items"]["properties"]["status"]["enum"])
    assert set(DATA["statuses"]) == enum
    found: set[str] = set()
    patterns = [
        r'status\s*=\s*"([a-z_]+)"',                       # status="written"
        r'"status":\s*"([a-z_]+)"',                         # {"status": "written"}
        r'status="[a-z_]+" if [^\n]* else "([a-z_]+)"',     # status="a" if x else "b"
        r'\.status\s*=\s*"([a-z_]+)"',                      # f.status = "skipped"
        r'StepResult\(step, "([a-z_]+)"',
        r'OutFile\(\w+, "([a-z_]+)"',
    ]
    for path in (ROOT / "mysuite").rglob("*.py"):
        if "tui" in path.parts:
            continue
        text = path.read_text()
        for pat in patterns:
            found |= set(re.findall(pat, text))
    missing = sorted(found - enum)
    assert not missing, f"add these statuses to schema.STATUSES: {missing}"


def test_exit_code_enum_includes_bad_usage():
    assert DATA["result"]["properties"]["exit_code"]["enum"] == [0, 1, 2, 3, 4]


def test_markdown_shows_choices_examples_and_flags():
    md = schema.markdown(DATA)
    section = md.split("## `mysuite qr make`", 1)[1].split("\n## ", 1)[0]
    assert "one of `l`, `m`, `q`, `h`" in section
    assert "writes files" in section
    assert "mysuite qr make" in section.split("Examples", 1)[1]
