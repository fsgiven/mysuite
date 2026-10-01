from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

from mysuite import sandbox
from mysuite.cli import app
from tests.helpers import SIMPLE_SVG, runner

need = pytest.mark.skipif(not all(shutil.which(t) for t in ("rsvg-convert", "gs", "magick", "exiftool")), reason="needs tools")


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.delenv("MYSUITE_ROOTS", raising=False)
    monkeypatch.delenv("MYSUITE_MAX_FILES", raising=False)
    yield
    sandbox.configure(None)


@pytest.fixture
def jail(tmp_path):
    root = tmp_path / "jail"
    root.mkdir()
    (root / "logo.svg").write_text(SIMPLE_SVG)
    subprocess.run(["magick", "-size", "20x20", "xc:red", f"PNG24:{root / 'a.png'}"], check=True)
    return root


def invoke(*args):
    res = runner.invoke(app, [str(a) for a in args])
    return res


def jdoc(res):
    return json.loads(res.stdout)


@need
def test_inside_the_root_works(jail):
    res = invoke("--allow", jail, "convert", jail / "a.png", "--to", "jpeg", "--json")
    assert res.exit_code == 0 and (jail / "a.jpg").exists()


@need
def test_output_outside_the_root_is_refused_with_exit_3_and_nothing_written(jail, tmp_path):
    res = invoke("--allow", jail, "export", jail / "logo.svg", "--formats", "png", "--sizes", "16",
                 "--out", tmp_path / "outside", "--json")
    assert res.exit_code == 3
    doc = jdoc(res)
    assert doc["exit_code"] == 3 and doc["ok"] is False and "outside the allowed" in doc["errors"][0]
    assert not (tmp_path / "outside").exists()


@need
def test_input_outside_the_root_is_refused(jail, tmp_path):
    other = tmp_path / "other.png"
    subprocess.run(["magick", "-size", "5x5", "xc:blue", f"PNG24:{other}"], check=True)
    res = invoke("--allow", jail, "convert", other, "--to", "jpeg", "--json")
    assert res.exit_code == 3 and not (tmp_path / "other.jpg").exists()


@need
def test_dotdot_template_cannot_climb_out(jail, tmp_path):
    cfg = jail / "c.toml"
    cfg.write_text('[export]\nnaming_template = "../../../../../esc_{size}{ext}"\n')
    res = invoke("--allow", jail, "export", jail / "logo.svg", "--config", cfg, "--formats", "png", "--sizes", "16",
                 "--out", jail / "o", "--json")
    assert res.exit_code != 0
    assert not list(tmp_path.glob("esc_*"))


@need
def test_symlink_pointing_out_of_the_root_is_refused(jail, tmp_path):
    secret = tmp_path / "secret.png"
    subprocess.run(["magick", "-size", "5x5", "xc:blue", f"PNG24:{secret}"], check=True)
    os.symlink(secret, jail / "link.png")
    res = invoke("--allow", jail, "convert", jail / "link.png", "--to", "jpeg", "--json")
    assert res.exit_code == 3


@need
def test_symlinked_output_folder_cannot_escape(jail, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, jail / "o")
    res = invoke("--allow", jail, "export", jail / "logo.svg", "--formats", "png", "--sizes", "16", "--out", jail / "o", "--json")
    assert res.exit_code == 3 and not list(outside.rglob("*.png"))


@need
def test_sandbox_flag_means_current_directory(jail, monkeypatch, tmp_path):
    monkeypatch.chdir(jail)
    res = invoke("--sandbox", "convert", jail / "a.png", "--to", "jpeg", "--json")
    assert res.exit_code == 0
    other = tmp_path / "o.png"
    subprocess.run(["magick", "-size", "5x5", "xc:blue", f"PNG24:{other}"], check=True)
    assert invoke("--sandbox", "convert", other, "--to", "jpeg", "--json").exit_code == 3


@need
def test_environment_variable_activates_the_sandbox(jail, tmp_path, monkeypatch):
    monkeypatch.setenv("MYSUITE_ROOTS", str(jail))
    other = tmp_path / "o.png"
    subprocess.run(["magick", "-size", "5x5", "xc:blue", f"PNG24:{other}"], check=True)
    assert invoke("convert", other, "--to", "jpeg", "--json").exit_code == 3
    assert invoke("convert", jail / "a.png", "--to", "jpeg", "--json").exit_code == 0


@need
def test_file_count_limit(jail, monkeypatch):
    for i in range(4):
        shutil.copy(jail / "a.png", jail / f"b{i}.png")
    monkeypatch.setenv("MYSUITE_ROOTS", str(jail))
    monkeypatch.setenv("MYSUITE_MAX_FILES", "3")
    res = invoke("convert", jail, "--to", "jpeg", "--json")
    assert res.exit_code == 3 and "limit" in jdoc(res)["errors"][0]
    assert not list(jail.glob("*.jpg"))


@need
def test_no_sandbox_by_default(tmp_path):
    p = tmp_path / "x.png"
    subprocess.run(["magick", "-size", "5x5", "xc:blue", f"PNG24:{p}"], check=True)
    assert invoke("convert", p, "--to", "jpeg").exit_code == 0


@need
def test_refusal_without_json_is_a_one_line_error_not_a_traceback(jail, tmp_path):
    res = invoke("--allow", jail, "export", jail / "logo.svg", "--formats", "png", "--sizes", "16", "--out", tmp_path / "x")
    assert res.exit_code == 3 and "Traceback" not in res.output and "refused" in (res.stdout + res.stderr)


def test_policy_unit_checks(tmp_path):
    pol = sandbox.Policy([tmp_path])
    pol.check(tmp_path / "a" / "b.png", write=True)
    with pytest.raises(sandbox.PolicyError):
        pol.check(tmp_path.parent / "x.png", write=False)
    with pytest.raises(sandbox.PolicyError):
        pol.check(tmp_path / ".." / "x.png", write=False)
