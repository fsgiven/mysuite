from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from mysuite.cli import app
from mysuite.pipeline import engine
from tests.helpers import SIMPLE_SVG, runner

need = pytest.mark.skipif(not all(shutil.which(t) for t in ("rsvg-convert", "gs", "magick", "exiftool", "oxipng")), reason="needs tools")


def parse(toml_dict, tmp_path):
    return engine.parse(toml_dict, base=tmp_path)


# ------------------------------------------------------------------ validation (nothing runs)
@pytest.mark.parametrize("data,fragment", [
    ({}, "at least one"),
    ({"step": []}, "at least one"),
    ({"inputs": ["a.svg"], "step": [{"tool": "nope"}]}, "unknown tool"),
    ({"inputs": ["a.svg"], "step": [{"tool": "export", "formatz": ["png"]}]}, "formatz"),
    ({"inputs": ["a.svg"], "step": [{"tool": "export", "dry_run": True}]}, "reserved"),
    ({"inputs": ["a.svg"], "step": [{"tool": "metadata"}]}, "action"),
    ({"inputs": ["a.svg"], "step": [{"tool": "metadata", "action": "explode"}]}, "action"),
    ({"step": [{"tool": "export"}]}, "needs input files"),
    ({"inputs": ["a.svg"], "step": [{"tool": "export", "id": "a"}, {"tool": "compress", "id": "a"}]}, "duplicate id"),
    ({"inputs": ["a.svg"], "step": [{"tool": "export"}, {"tool": "compress", "from": "ghost"}]}, "earlier step id"),
    ({"inputs": ["a.svg"], "colour": 1, "step": [{"tool": "export"}]}, "unknown top-level"),
    ({"inputs": "a.svg", "step": [{"tool": "export"}]}, "list"),
    ({"inputs": ["a"], "step": [{"tool": "export"}] * 25}, "too many"),
    ({"inputs": ["a"], "step": [{"tool": "export", "only": "png"}]}, "only"),
    ({"inputs": ["a"], "step": ["export"]}, "tool"),
])
def test_bad_pipelines_are_rejected_before_anything_runs(data, fragment, tmp_path):
    with pytest.raises(engine.PipelineError, match=fragment):
        parse(data, tmp_path)


def test_typo_error_lists_the_valid_options(tmp_path):
    with pytest.raises(engine.PipelineError) as exc:
        parse({"inputs": ["a"], "step": [{"tool": "export", "cmyk_mod": "clean"}]}, tmp_path)
    assert "cmyk_mode" in str(exc.value)


def test_first_step_reads_the_inputs_and_later_steps_the_previous_outputs(tmp_path):
    p = parse({"inputs": ["a.svg"], "step": [{"tool": "export"}, {"tool": "compress", "codec": "oxipng"}]}, tmp_path)
    assert [s.source for s in p.steps] == ["inputs", "previous"]


# ---------------------------------------------------------------- argv building
def argv(step_dict, tmp_path, **kw):
    p = parse({"inputs": ["a.svg"], "step": [step_dict]}, tmp_path)
    return engine.build_argv(p.steps[0], [Path("/x/a.svg")], overwrite=kw.get("overwrite", False), dry_run=kw.get("dry_run", False))


def test_argv_lists_become_csv_and_repeatables_repeat(tmp_path):
    a = argv({"tool": "export", "formats": ["png", "pdf"], "sizes": [64, 512], "recolor": ["#d00=#00f", "#fff=#000"],
              "cmyk_mode": "clean:10", "strict": True}, tmp_path)
    assert a[:1] == ["export"] and a[-3:] == ["--json", "--", "/x/a.svg"]
    assert a[a.index("--formats") + 1] == "png,pdf" and a[a.index("--sizes") + 1] == "64,512"
    assert a.count("--recolor") == 2 and a[a.index("--cmyk-mode") + 1] == "clean:10" and "--strict" in a


def test_argv_dict_recolor_and_false_flags(tmp_path):
    a = argv({"tool": "export", "recolor": {"#dd0000": "#0057b8"}, "overwrite": False}, tmp_path)
    assert a[a.index("--recolor") + 1] == "#dd0000=#0057b8" and "--no-overwrite" in a


def test_argv_overwrite_only_when_pipeline_allows_and_dry_run_flag(tmp_path):
    assert "--overwrite" not in argv({"tool": "convert", "to": "png"}, tmp_path)
    assert "--overwrite" in argv({"tool": "convert", "to": "png"}, tmp_path, overwrite=True)
    assert "--dry-run" in argv({"tool": "convert", "to": "png"}, tmp_path, dry_run=True)


def test_metadata_and_enhance_subcommands(tmp_path):
    assert argv({"tool": "metadata", "action": "strip"}, tmp_path)[:2] == ["metadata", "strip"]
    assert argv({"tool": "enhance", "preset": "gentle"}, tmp_path)[:2] == ["enhance", "run"]


# ---------------------------------------------------------------------- end to end
def md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


@pytest.fixture
def work(tmp_path):
    (tmp_path / "logo.svg").write_text(SIMPLE_SVG)
    return tmp_path


def write_pipeline(work, text, name="p.toml"):
    path = work / name
    path.write_text(text)
    return path


def run_pipeline(path, *args):
    res = runner.invoke(app, ["pipeline", "run", str(path), *args, "--json"])
    return res, json.loads(res.stdout)


RELEASE = '''
name = "release"
inputs = ["logo.svg"]

[[step]]
tool = "export"
formats = ["png"]
sizes = [64, 128]
out = "dist"

[[step]]
tool = "metadata"
action = "randomize"
only = ["png"]

[[step]]
tool = "compress"
codec = "oxipng"
only = ["png"]
'''


@need
def test_export_then_metadata_then_compress_chain(work):
    before = md5(work / "logo.svg")
    res, doc = run_pipeline(write_pipeline(work, RELEASE))
    assert res.exit_code == 0 and doc["ok"], doc
    assert [i["status"] for i in doc["items"]] == ["ok", "ok", "ok"]
    final = sorted(Path(o).name for o in doc["items"][2]["outputs"])
    assert final == ["logo_128_randomized_compressed.png", "logo_64_randomized_compressed.png"]
    assert all(Path(o).is_file() for o in doc["items"][2]["outputs"])
    assert md5(work / "logo.svg") == before                       # the input is untouched
    # every step fed the next one
    assert set(doc["items"][1]["inputs"]) == set(doc["items"][0]["outputs"])


@need
def test_second_run_skips_instead_of_overwriting(work):
    p = write_pipeline(work, RELEASE)
    run_pipeline(p)
    first = {f: md5(f) for f in (work / "dist").rglob("*.png")}
    res, doc = run_pipeline(p)
    assert res.exit_code == 0
    assert {f: md5(f) for f in (work / "dist").rglob("*.png")} == first
    statuses = {i["status"] for it in doc["items"] for i in it["result"]["items"]}
    assert statuses == {"skipped_existing"}


@need
def test_dry_run_plans_step_one_exactly_and_writes_nothing(work):
    res, doc = run_pipeline(write_pipeline(work, RELEASE), "--dry-run")
    assert res.exit_code == 0 and doc["dry_run"] is True
    assert not (work / "dist").exists()
    step1 = doc["items"][0]
    assert [i["status"] for i in step1["result"]["items"]] == ["planned", "planned"]
    assert all("not known until it runs" in (it["note"] or "") for it in doc["items"][1:])
    planned = {Path(o).name for o in step1["outputs"]}
    res, real = run_pipeline(write_pipeline(work, RELEASE, "p2.toml"))
    assert planned == {Path(o).name for o in real["items"][0]["outputs"]}       # plan == what the real run wrote


@need
def test_a_failing_step_skips_the_rest_unless_continue_on_error(work):
    bad = RELEASE.replace('codec = "oxipng"', 'codec = "nonsense"')
    res, doc = run_pipeline(write_pipeline(work, bad))
    assert res.exit_code == 1 and [i["status"] for i in doc["items"]] == ["ok", "ok", "failed"]

    text = '''
inputs = ["logo.svg"]
continue_on_error = true

[[step]]
tool = "convert"
to = "nonsense"

[[step]]
tool = "export"
formats = ["png"]
sizes = [32]
out = "o2"
from = "inputs"
'''
    res, doc = run_pipeline(write_pipeline(work, text, "c.toml"))
    assert [i["status"] for i in doc["items"]] == ["failed", "ok"] and res.exit_code == 1
    assert (work / "o2").exists()


@need
def test_fail_fast_marks_later_steps_skipped(work):
    text = '''
inputs = ["logo.svg"]
[[step]]
tool = "convert"
to = "nonsense"
[[step]]
tool = "compress"
codec = "oxipng"
'''
    res, doc = run_pipeline(write_pipeline(work, text))
    assert [i["status"] for i in doc["items"]] == ["failed", "skipped"]


@need
def test_relative_paths_are_relative_to_the_pipeline_file_not_the_cwd(work, tmp_path_factory, monkeypatch):
    monkeypatch.chdir(tmp_path_factory.mktemp("elsewhere"))
    res, doc = run_pipeline(write_pipeline(work, RELEASE))
    assert res.exit_code == 0 and (work / "dist").exists()


@need
def test_from_inputs_and_step_ids(work):
    text = '''
inputs = ["*.svg"]
[[step]]
id = "icons"
tool = "export"
formats = ["png"]
sizes = [32]
out = "a"
[[step]]
id = "pdfs"
tool = "export"
formats = ["pdf"]
sizes = [32]
out = "b"
from = "inputs"
[[step]]
tool = "compress"
codec = "oxipng"
from = "icons"
only = ["png"]
'''
    res, doc = run_pipeline(write_pipeline(work, text))
    assert res.exit_code == 0, doc
    assert doc["items"][2]["inputs"] == doc["items"][0]["outputs"]
    assert all(o.endswith(".pdf") for o in doc["items"][1]["outputs"])


@need
def test_json_pipeline_file_and_global_overwrite(work):
    data = {"inputs": ["logo.svg"], "overwrite": True,
            "step": [{"tool": "export", "formats": ["png"], "sizes": [32], "out": "o"}]}
    p = write_pipeline(work, json.dumps(data), "p.json")
    assert run_pipeline(p)[0].exit_code == 0
    res, doc = run_pipeline(p)
    assert doc["items"][0]["result"]["items"][0]["status"] == "written"       # overwrite = true replaced it


@need
def test_the_sandbox_applies_to_every_step(work, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    text = f'''
inputs = ["logo.svg"]
[[step]]
tool = "export"
formats = ["png"]
sizes = [32]
out = "{outside}/o"
'''
    res = runner.invoke(app, ["--allow", str(work), "pipeline", "run", str(write_pipeline(work, text)), "--json"])
    doc = json.loads(res.stdout)
    assert res.exit_code == 3 and not list(outside.rglob("*"))
    assert any("outside the allowed" in e for e in doc["errors"])


@need
def test_unknown_input_glob_is_a_clean_error(work):
    text = 'inputs = ["nothing-*.svg"]\n[[step]]\ntool = "export"\n'
    res, doc = run_pipeline(write_pipeline(work, text))
    assert res.exit_code == 1 and "matches nothing" in doc["errors"][0]


def test_unreadable_pipeline_file(tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text("this is = = not toml")
    res = runner.invoke(app, ["pipeline", "run", str(bad), "--json"])
    assert res.exit_code == 1 and "can't read pipeline" in json.loads(res.stdout)["errors"][0]
