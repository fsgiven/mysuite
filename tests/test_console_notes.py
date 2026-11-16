"""P0-5: a note is not a skip. "⚠ skipping" is only for things that were really not done."""
from __future__ import annotations

import json

from PIL import Image

from mysuite.cli import app
from mysuite.utils import console as console_mod
from mysuite.utils import jsonout
from tests.helpers import runner


def test_log_note_is_a_warning_without_the_word_skipping(capsys):
    console_mod.log_note("image turned 90°")
    console_mod.log_skip("png/cmyk — not supported")
    out = capsys.readouterr().out
    note, skip = [line for line in out.splitlines() if line.strip()]
    assert "image turned 90°" in note and "skipping" not in note.lower() and "⚠" in note
    assert "skipping" in skip.lower()


def test_print_auto_rotate_note_does_not_say_skipping_although_the_file_is_written(tmp_path):
    src = tmp_path / "wide.png"
    Image.new("RGB", (300, 200), "red").save(src)
    res = runner.invoke(app, ["print", str(src), "--size", "200x300", "--auto-rotate"])
    assert res.exit_code == 0, res.output
    assert (tmp_path / "wide_print.png").is_file()
    assert "turned" in res.output and "skipping" not in res.output.lower(), res.output
    res = runner.invoke(app, ["print", str(src), "--size", "200x300", "--auto-rotate", "--overwrite", "--json"])
    assert any("turned" in w for w in json.loads(res.stdout)["warnings"])     # still reported as a warning in JSON


def test_every_log_helper_a_module_uses_is_imported_there():
    """`(log_skip if … else …)(msg)` uses a helper without calling it by name; dropping its import broke svg import."""
    import ast
    from pathlib import Path

    import mysuite

    missing = []
    for path in Path(mysuite.__file__).parent.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        defined = {a.asname or a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
        defined |= {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id.startswith("log_")}
        missing += [f"{path.name}: {name}" for name in used - defined]
    assert not missing, missing
