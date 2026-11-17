"""WS-E pipeline work: more tools as steps, intermediate clean-up, "did you mean", `pipeline validate`,
dry-run wording, and read-only steps that really run in a dry run."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from PIL import Image

from mysuite.cli import app
from mysuite.pipeline import engine
from tests.helpers import SIMPLE_SVG, runner

need = pytest.mark.skipif(not all(shutil.which(t) for t in ("rsvg-convert", "gs", "magick", "exiftool", "oxipng")), reason="needs tools")


def parse(data, tmp_path):
    return engine.parse(data, base=tmp_path)


@pytest.fixture
def work(tmp_path):
    (tmp_path / "logo.svg").write_text(SIMPLE_SVG)
    Image.new("RGB", (60, 40), "#dd0000").save(tmp_path / "pic.png")
    Image.new("RGB", (60, 40), "#dd0001").save(tmp_path / "pic2.png")
    return tmp_path


def write(work, text, name="p.toml"):
    (work / name).write_text(text)
    return work / name


def run_pipeline(path, *args):
    res = runner.invoke(app, ["pipeline", "run", str(path), *args, "--json"])
    return res, json.loads(res.stdout)


# ------------------------------------------------------------------------- more tools as steps
@pytest.mark.parametrize("step,command", [
    ({"tool": "print", "size": "100x100"}, ["print"]),
    ({"tool": "rename", "pattern": "x_{n}{ext}"}, ["rename"]),
    ({"tool": "sheet", "out": "s.png"}, ["sheet"]),
    ({"tool": "profile", "to": "srgb"}, ["profile"]),
    ({"tool": "diff"}, ["diff"]),
    ({"tool": "qr", "action": "read"}, ["qr", "read"]),
])
def test_new_tools_are_accepted_as_steps(step, command, tmp_path):
    p = parse({"inputs": ["a.png"], "step": [step]}, tmp_path)
    assert p.steps[0].command == command


def test_qr_make_needs_text_and_no_inputs(tmp_path):
    p = parse({"step": [{"tool": "qr", "action": "make", "text": "https://example.com", "out": "qr.png"}]}, tmp_path)
    argv = engine.build_argv(p.steps[0], engine.step_inputs(p.steps[0], []), overwrite=False, dry_run=False)
    assert argv[:2] == ["qr", "make"] and argv[-1] == "https://example.com"
    with pytest.raises(engine.PipelineError, match="text"):
        parse({"step": [{"tool": "qr", "action": "make"}]}, tmp_path)


# ------------------------------------------------------------------------------- did you mean
@pytest.mark.parametrize("data,hint", [
    ({"inputs": ["a.svg"], "step": [{"tool": "export", "format": ["png"]}]}, "did you mean 'formats'"),
    ({"inputs": ["a.svg"], "step": [{"tool": "exprot"}]}, "did you mean 'export'"),
    ({"inputs": ["a.svg"], "step": [{"tool": "pdf", "action": "rotat"}]}, "did you mean 'rotate'"),
    ({"inputs": ["a.svg"], "stpe": [], "step": [{"tool": "export"}]}, "did you mean 'step'"),
])
def test_typos_get_a_suggestion(data, hint, tmp_path):
    with pytest.raises(engine.PipelineError, match=hint):
        parse(data, tmp_path)


def test_a_value_outside_the_declared_choices_is_rejected(tmp_path):
    with pytest.raises(engine.PipelineError, match="one of l, m, q, h"):
        parse({"step": [{"tool": "qr", "action": "make", "text": "x", "error": "z"}]}, tmp_path)


# ------------------------------------------------------------------------------------- validate
def test_validate_catches_a_typo_and_runs_nothing(work):
    p = write(work, 'inputs = ["logo.svg"]\n[[step]]\ntool = "export"\nformat = ["png"]\nout = "dist"\n')
    res = runner.invoke(app, ["pipeline", "validate", str(p), "--json"])
    doc = json.loads(res.stdout)
    assert res.exit_code == 1 and "did you mean 'formats'" in doc["errors"][0]
    assert not (work / "dist").exists()


def test_validate_describes_a_good_pipeline(work):
    p = write(work, 'inputs = ["logo.svg"]\n[[step]]\ntool = "export"\nformats = ["png"]\nsizes = [32]\nout = "dist"\n'
                    '[[step]]\ntool = "pdf"\naction = "rotate"\ndegrees = 90\n')
    res = runner.invoke(app, ["pipeline", "validate", str(p), "--json"])
    doc = json.loads(res.stdout)
    assert res.exit_code == 0, doc
    assert [i["status"] for i in doc["items"]] == ["ok", "ok"]
    assert doc["items"][0]["has_dry_run"] is True and doc["items"][1]["has_dry_run"] is False
    assert doc["items"][0]["argv"][:3] == ["mysuite", "export", "--formats"]
    assert doc["inputs"] == [str(work / "logo.svg")]
    assert not (work / "dist").exists()


def test_validate_reports_inputs_that_match_nothing(work):
    p = write(work, 'inputs = ["nothing-*.svg"]\n[[step]]\ntool = "export"\n')
    res = runner.invoke(app, ["pipeline", "validate", str(p), "--json"])
    assert res.exit_code == 1 and "matches nothing" in json.loads(res.stdout)["errors"][0]


# ---------------------------------------------------------------------------- dry-run wording
def test_dry_run_notes_say_what_happens(work):
    p = write(work, 'inputs = ["pic.png"]\n[[step]]\ntool = "pdf"\naction = "from-images"\n'
                    '[[step]]\ntool = "convert"\nto = "webp"\n')
    res, doc = run_pipeline(p, "--dry-run")
    first, second = doc["items"]
    assert first["status"] == "skipped"
    assert "a real run will run it" in first["note"].lower() and "would write files" in first["note"]
    assert second["status"] == "planned" and "step 1" in second["note"] and "stepstep" not in second["note"]


def test_a_read_only_step_really_runs_in_a_dry_run(work):
    p = write(work, 'inputs = ["pic.png", "pic2.png"]\n[[step]]\ntool = "dupes"\n')
    res, doc = run_pipeline(p, "--dry-run")
    step = doc["items"][0]
    assert res.exit_code == 0 and step["status"] == "planned" and step["exit_code"] == 0
    assert step["result"]["command"] == "dupes"


# ------------------------------------------------------------------------------------- clean-up
CHAIN = '''
inputs = ["pic.png"]
{extra}
[[step]]
tool = "transform"
resize = "30"

[[step]]
tool = "print"
size = "20x20"
fit = "cover"
'''


def test_intermediate_files_are_removed_by_default(work):
    res, doc = run_pipeline(write(work, CHAIN.format(extra="")))
    assert res.exit_code == 0, doc
    first, last = doc["items"]
    intermediate = Path(first["outputs"][0])
    assert first["removed"] == [str(intermediate)] and not intermediate.exists()
    assert all(Path(o).is_file() for o in last["outputs"]) and last["removed"] == []
    assert (work / "pic.png").is_file()                                  # the original stays


def test_keep_intermediate_keeps_them(work):
    res, doc = run_pipeline(write(work, CHAIN.format(extra="keep_intermediate = true")))
    assert res.exit_code == 0 and doc["items"][0]["removed"] == []
    assert Path(doc["items"][0]["outputs"][0]).is_file()
    res, doc = run_pipeline(write(work, CHAIN.format(extra=""), "q.toml"), "--keep-intermediate")
    assert Path(doc["items"][0]["outputs"][0]).is_file()


def test_a_file_that_already_existed_is_never_removed(work):
    (work / "pic_transformed.png").write_bytes((work / "pic.png").read_bytes())
    res, doc = run_pipeline(write(work, CHAIN.format(extra="")))
    assert doc["items"][0]["result"]["items"][0]["status"] == "skipped_existing"
    assert (work / "pic_transformed.png").is_file() and doc["items"][0]["removed"] == []


def test_nothing_is_removed_when_a_later_step_fails(work):
    bad = CHAIN.format(extra="").replace('size = "20x20"', 'size = "nonsense"')
    res, doc = run_pipeline(write(work, bad))
    assert res.exit_code == 1 and Path(doc["items"][0]["outputs"][0]).is_file()


def test_outputs_not_consumed_by_a_later_step_are_kept(work):
    text = '''
inputs = ["pic.png"]
[[step]]
tool = "convert"
to = ["webp", "jpeg"]
[[step]]
tool = "transform"
resize = "20"
only = ["webp"]
'''
    res, doc = run_pipeline(write(work, text))
    assert res.exit_code == 0, doc
    removed = doc["items"][0]["removed"]
    assert [Path(r).suffix for r in removed] == [".webp"]
    assert (work / "pic.jpg").is_file() or (work / "pic.jpeg").is_file()


# --------------------------------------------------------------------- qr and diff steps, end to end
def test_qr_make_feeds_the_next_step(work):
    text = '''
[[step]]
tool = "qr"
action = "make"
text = "https://example.com"
out = "qr.png"
[[step]]
tool = "print"
size = "300x300"
'''
    res, doc = run_pipeline(write(work, text))
    assert res.exit_code == 0, doc
    assert Path(doc["items"][1]["outputs"][0]).name == "qr_print.png"


def test_diff_step_compares_against_a_reference(work):
    text = '''
inputs = ["pic.png"]
[[step]]
tool = "diff"
against = "pic2.png"
'''
    res, doc = run_pipeline(write(work, text))
    assert res.exit_code == 0, doc
    assert doc["items"][0]["result"]["items"][0]["identical"] is False


def test_diff_step_needs_exactly_two_files(work):
    res, doc = run_pipeline(write(work, 'inputs = ["pic.png"]\n[[step]]\ntool = "diff"\n'))
    assert res.exit_code == 1 and "exactly two" in doc["items"][0]["note"]


def test_with_overwrite_nothing_is_removed(work):
    """overwrite = true may have replaced a file the user already had; deleting it afterwards would lose it."""
    (work / "pic_transformed.png").write_bytes(b"the user's own file")
    res, doc = run_pipeline(write(work, CHAIN.format(extra="overwrite = true")))
    assert res.exit_code == 0, doc
    assert (work / "pic_transformed.png").is_file() and doc["items"][0]["removed"] == []
    assert "overwrite" in " ".join(doc["warnings"])


# --------------------------------------- svg import -> optimise (CI-only test in test_vector.py needs poppler)
def _fake_import(monkeypatch):
    """Stand in for `svg import` (which needs pdftocairo): write brand.svg like the real child would."""
    real = engine._run_child

    def run_child(argv, cwd, profile=None):
        if argv[:2] != ["svg", "import"]:
            return real(argv, cwd, profile)
        src = Path(argv[-1])
        out = src.with_suffix(".svg")
        out.write_text(SIMPLE_SVG)
        return 0, {"schema_version": 1, "command": "svg-import", "ok": True, "exit_code": 0, "warnings": [], "errors": [],
                   "items": [{"input": str(src), "output": str(out), "status": "written"}]}, ""
    monkeypatch.setattr(engine, "_run_child", run_child)


IMPORT_OPTIMISE = '''
inputs = ["brand.ai"]
{extra}
[[step]]
tool = "svg"
action = "import"
[[step]]
tool = "svg"
action = "optimise"
verify = false
'''


@need
def test_import_then_optimise_removes_the_plain_import_by_default(work, monkeypatch):
    _fake_import(monkeypatch)
    (work / "brand.ai").write_bytes(b"%PDF-1.4 stand-in")
    res, doc = run_pipeline(write(work, IMPORT_OPTIMISE.format(extra="")))
    assert res.exit_code == 0, doc
    assert (work / "brand.min.svg").is_file() and not (work / "brand.svg").exists()
    assert doc["items"][0]["removed"] == [str(work / "brand.svg")]
    assert (work / "brand.ai").is_file()


@need
def test_import_then_optimise_keeps_both_with_keep_intermediate(work, monkeypatch):
    _fake_import(monkeypatch)
    (work / "brand.ai").write_bytes(b"%PDF-1.4 stand-in")
    res, doc = run_pipeline(write(work, IMPORT_OPTIMISE.format(extra="keep_intermediate = true")))
    assert res.exit_code == 0, doc
    assert (work / "brand.svg").is_file() and (work / "brand.min.svg").is_file()
