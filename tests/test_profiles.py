from __future__ import annotations

import json
import shutil
import subprocess

import pytest
from PIL import Image

from mysuite import profiles as P
from mysuite.cli import app
from mysuite.config import Config, load_config
from tests.helpers import SIMPLE_SVG, runner

need = pytest.mark.skipif(shutil.which("rsvg-convert") is None, reason="needs rsvg-convert")
need_meta = pytest.mark.skipif(not all(shutil.which(t) for t in ("exiftool", "magick")), reason="needs exiftool/magick")

TOKENS = ':root{--brand-red:#dd0000;--brand-blue:#0057b8;--ink:#222628}[data-theme="dark"]{--brand-red:#ff6b6b;--brand-blue:#6bb0ff;--ink:#ffffff}'


@pytest.fixture(autouse=True)
def _no_profile(monkeypatch):
    monkeypatch.delenv("MYSUITE_PROFILE", raising=False)
    P.activate(None)
    yield
    P.activate(None)


@pytest.fixture
def work(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "logo.svg").write_text(SIMPLE_SVG)
    (tmp_path / "tokens.css").write_text(TOKENS)
    return tmp_path


def write_config(work, body):
    (work / "mysuite.toml").write_text(body)


ACME = '''
[profiles.acme]
description = "Acme"
tokens = "tokens.css"
variants = "default,mono-white"
compress_preset = "web"
[profiles.acme.export]
formats = ["png"]
sizes = [48]
out_dir = "acme-out"
[profiles.acme.metadata]
policy = ["strip"]
author = "Acme Corp"
copyright = "(c) 2026 Acme"
'''


def run(*args, global_args=()):
    res = runner.invoke(app, [*[str(a) for a in global_args], *[str(a) for a in args], "--json"])
    return res, json.loads(res.stdout)


# ------------------------------------------------------------------------- validation
@pytest.mark.parametrize("data,fragment", [
    ({"nope": 1}, "unknown key"), ({"export": {"formatz": []}}, "unknown export setting"), ({"export": 3}, "must be a table"),
    ({"metadata": {"polcy": "strip"}}, "metadata"), ({"metadata": {"policy": ["shred"]}}, "policy"),
    ({"metadata": {"policy": []}}, "policy"), ({"metadata": {"policy": ["credit"]}}, "needs metadata.author"),
    ({"theme": "blue"}, "theme"), ({"variants": "sepia"}, "unknown variant"), ({"tokens": 5}, "text"), ({"allow": [1]}, "text"),
])
def test_bad_profiles_are_rejected_with_a_reason(data, fragment):
    with pytest.raises(P.ProfileError, match=fragment):
        P.parse_profile("x", data)


def test_a_good_profile_parses_and_policy_may_be_a_string():
    p = P.parse_profile("x", {"metadata": {"policy": "strip", "author": "A"}, "variants": "default,negative", "theme": "dark"})
    assert p.policy == ["strip"] and p.theme == "dark"


def test_unknown_profile_names_the_defined_ones(work):
    write_config(work, ACME)
    P.activate("nope")
    with pytest.raises(P.ProfileError, match="acme"):
        P.resolve(load_config(None))
    with pytest.raises(P.ProfileError, match="none defined"):
        P.resolve(Config())


def test_no_profile_selected_means_none(work):
    write_config(work, ACME)
    assert P.resolve(load_config(None)) is None
    P.activate("acme")
    assert P.resolve(load_config(None)).name == "acme"


# ------------------------------------------------------------------------ export layering
@need
def test_profile_supplies_export_defaults_and_tokens(work):
    write_config(work, ACME)
    res, doc = run("export", "logo.svg", global_args=["--profile", "acme"])
    assert res.exit_code == 0, doc
    assert doc["profile"] == "acme" and doc["tokens"]["count"] >= 3
    outs = sorted(i["output"].split("acme-out/")[1] for i in doc["items"])
    assert outs == ["logo/mono-white/png/rgb/logo_48.png", "logo/png/rgb/logo_48.png"]       # profile: formats, sizes, out_dir, variants


@need
def test_layering_flags_beat_preset_beat_profile(work):
    write_config(work, ACME + '\n[presets.big]\nsizes = [96]\n')
    res, doc = run("export", "logo.svg", "--variants", "default", global_args=["--profile", "acme"])
    assert [i["output"].endswith("logo_48.png") for i in doc["items"]] == [True]                # flag beats the profile's variants
    res, doc = run("export", "logo.svg", "--preset", "big", "--variants", "default", global_args=["--profile", "acme"])
    assert doc["items"][0]["output"].endswith("logo_96.png")                                    # preset beats the profile's sizes
    res, doc = run("export", "logo.svg", "--preset", "big", "--sizes", "128", "--variants", "default", global_args=["--profile", "acme"])
    assert doc["items"][0]["output"].endswith("logo_128.png")                                   # flag beats the preset


@need
def test_profile_from_the_environment_and_without_a_profile_nothing_changes(work, monkeypatch):
    write_config(work, ACME)
    monkeypatch.setenv("MYSUITE_PROFILE", "acme")
    assert run("export", "logo.svg", "--dry-run")[1]["profile"] == "acme"
    monkeypatch.delenv("MYSUITE_PROFILE")
    res, doc = run("export", "logo.svg", "--dry-run")
    assert "profile" not in doc and not any("acme-out" in i["output"] for i in doc["items"])


@need
def test_unknown_profile_is_a_clean_error_and_writes_nothing(work):
    write_config(work, ACME)
    res, doc = run("export", "logo.svg", global_args=["--profile", "ghost"])
    assert res.exit_code == 1 and "ghost" in doc["errors"][0] and not (work / "acme-out").exists()


@need
def test_profile_in_a_broken_state_fails_the_command_not_silently(work):
    write_config(work, '[profiles.bad]\nunknown_key = 1\n')
    res, doc = run("export", "logo.svg", global_args=["--profile", "bad"])
    assert res.exit_code == 1 and "unknown key" in doc["errors"][0]


@need
def test_profile_tokens_enable_the_negative_variant(work):
    write_config(work, '[profiles.n]\ntokens = "tokens.css"\nvariants = "default,negative"\n')
    res, doc = run("export", "logo.svg", "--formats", "png", "--sizes", "50", global_args=["--profile", "n"])
    assert res.exit_code == 0 and {i["variant"] for i in doc["items"]} == {"", "negative"}


# -------------------------------------------------------------------- sandbox from profile
def test_profile_allow_folders_become_the_sandbox(work, tmp_path):
    jail = work / "jail"
    jail.mkdir()
    write_config(work, f'[profiles.j]\nallow = ["{jail}"]\n')
    other = work / "o.png"
    Image.new("RGB", (4, 4)).save(other)
    res = runner.invoke(app, ["--profile", "j", "convert", str(other), "--to", "jpeg", "--json"])
    assert res.exit_code == 3 and not (work / "o.jpg").exists()


# ---------------------------------------------------------------------- compress/enhance
@pytest.mark.skipif(not shutil.which("magick"), reason="needs magick")
def test_profile_default_presets_for_compress_and_enhance(work):
    write_config(work, '[profiles.p]\ncompress_preset = "nonexistent"\nenhance_preset = "gentle"\n')
    Image.new("RGB", (32, 32), "red").save(work / "a.png")
    res, doc = run("compress", "a.png", global_args=["--profile", "p"])
    assert res.exit_code == 1 and "nonexistent" in " ".join(doc["errors"])                    # the profile's preset was used (and is unknown)
    res, doc = run("enhance", "run", "a.png", "--scale", "1", "--backend", "classical", global_args=["--profile", "p"])
    assert res.exit_code == 0


# ------------------------------------------------------------------------- metadata
@need_meta
def test_metadata_apply_follows_the_profile_policy(work):
    subprocess.run(["magick", "-size", "40x30", "xc:red", "p.jpg"], check=True)
    subprocess.run(["exiftool", "-q", "-overwrite_original", "-GPSLatitude=1", "-GPSLatitudeRef=N", "-Make=X", "p.jpg"], check=True)
    write_config(work, ACME)
    res, doc = run("metadata", "apply", "p.jpg", global_args=["--profile", "acme"])
    assert res.exit_code == 0 and doc["policy"] == ["strip"] and doc["profile"] == "acme"
    out = doc["items"][0]["output"]
    assert out.endswith("p_stripped.jpg")
    tags = subprocess.run(["exiftool", "-GPS:all", "-Make", out], capture_output=True, text=True).stdout
    assert tags.strip() == ""


@need_meta
def test_metadata_apply_policy_flag_chains_steps_and_dry_run(work):
    subprocess.run(["magick", "-size", "40x30", "xc:red", "p.jpg"], check=True)
    res, doc = run("metadata", "apply", "p.jpg", "--policy", "strip,randomize", "--dry-run")
    assert doc["items"][0]["output"].endswith("p_stripped_randomized.jpg") and not list(work.glob("*_stripped*"))
    res, doc = run("metadata", "apply", "p.jpg", "--policy", "strip,randomize")
    assert (work / "p_stripped_randomized.jpg").exists() and (work / "p_stripped.jpg").exists()
    cam = subprocess.run(["exiftool", "-s3", "-Make", str(work / "p_stripped_randomized.jpg")], capture_output=True, text=True).stdout.strip()
    assert cam                                                                                  # decoy camera written after the strip


@pytest.mark.skipif(not all(shutil.which(t) for t in ("exiftool", "magick", "c2patool")), reason="needs c2patool")
def test_metadata_apply_strip_then_credit_from_the_profile(work):
    subprocess.run(["magick", "-size", "40x30", "xc:red", "p.jpg"], check=True)
    write_config(work, '[profiles.c]\n[profiles.c.metadata]\npolicy = ["strip", "credit"]\nauthor = "Acme Corp"\ncopyright = "(c) Acme"\n')
    res, doc = run("metadata", "apply", "p.jpg", global_args=["--profile", "c"])
    assert res.exit_code == 0, doc
    final = work / "p_stripped_credited.jpg"
    assert final.exists()
    manifest = subprocess.run(["c2patool", str(final)], capture_output=True, text=True).stdout
    assert "Acme Corp" in manifest


@need_meta
def test_metadata_apply_errors_and_credit_author_fallback(work):
    subprocess.run(["magick", "-size", "40x30", "xc:red", "p.jpg"], check=True)
    assert run("metadata", "apply", "p.jpg")[0].exit_code == 1                                 # no policy at all
    assert run("metadata", "apply", "p.jpg", "--policy", "shred")[0].exit_code == 1
    res, doc = run("metadata", "apply", "p.jpg", "--policy", "credit")
    assert res.exit_code == 1 and "author" in doc["errors"][0]
    res, doc = run("metadata", "credit", "p.jpg")
    assert res.exit_code == 1 and "author" in doc["errors"][0]


# ----------------------------------------------------------------------------- CLI group
def test_profiles_save_list_show_delete_round_trip(work):
    res, doc = run("profiles", "save", "acme", "--tokens", "tokens.css", "--brand", "x", "--variants", "default,negative",
                   "--formats", "png,pdf", "--sizes", "64,512,5cm", "--colour-profiles", "rgb,cmyk", "--cmyk-mode", "clean:10",
                   "--policy", "strip,credit", "--author", "Acme", "--allow", "/tmp/x", "--description", "Acme kit")
    assert res.exit_code == 0, doc
    text = (work / "mysuite.toml").read_text()
    assert 'sizes = [64, 512, "5cm"]' in text and 'policy = ["strip", "credit"]' in text
    cfg = load_config(None)
    assert P.parse_profile("acme", cfg.profiles["acme"]).export["cmyk_mode"] == "clean:10"
    res, doc = run("profiles", "list")
    assert doc["items"][0]["name"] == "acme" and doc["items"][0]["brand"] == "x"
    res, doc = run("profiles", "show", "acme")
    assert doc["items"][0]["metadata"]["author"] == "Acme"
    again = run("profiles", "save", "acme", "--brand", "y")
    assert again[0].exit_code == 1 and "already exists" in again[1]["errors"][0]
    assert run("profiles", "save", "acme", "--brand", "y", "--overwrite")[0].exit_code == 0
    assert run("profiles", "show", "acme")[1]["items"][0]["brand"] == "y"
    assert run("profiles", "delete", "acme")[0].exit_code == 0 and "acme" not in (work / "mysuite.toml").read_text()
    assert run("profiles", "delete", "acme")[0].exit_code == 1 and run("profiles", "show", "acme")[0].exit_code == 1


def test_save_validates_and_keeps_the_rest_of_the_file(work):
    write_config(work, "# my notes\n[export]\ndpi = 150\n\n[presets.keep]\nsizes = [10]\n")
    assert run("profiles", "save", "bad", "--policy", "credit")[0].exit_code == 1            # credit needs an author
    assert run("profiles", "save", "bad", "--theme", "blue")[0].exit_code == 1
    assert run("profiles", "save", "ok", "--brand", "b")[0].exit_code == 0
    text = (work / "mysuite.toml").read_text()
    assert "# my notes" in text and "dpi = 150" in text and "[presets.keep]" in text and "[profiles.ok]" in text and "bad" not in text


def test_profiles_check_reports_each_problem(work):
    write_config(work, '[profiles.a]\ntokens = "tokens.css"\nbrand = "zzz"\nallow = ["/nonexistent-folder-xyz"]\n[profiles.a.metadata]\npolicy = ["strip"]\n'
                       '[profiles.r]\ntokens = "git+https://example.invalid/r@v1"\n')
    res, doc = run("profiles", "check", "a")
    by = {i["check"]: i for i in doc["items"]}
    assert res.exit_code == 0 or res.exit_code == 1
    assert by["token source loads"]["status"] == "ok"
    assert by["allowed folder exists: /nonexistent-folder-xyz"]["status"] == "problem"
    res, doc = run("profiles", "check", "r")                                                    # remote source: not fetched without --online
    assert any("not fetched" in w for w in doc["warnings"])
    assert run("profiles", "check", "ghost")[0].exit_code == 1


def test_a_valid_local_profile_checks_clean(work):
    write_config(work, '[profiles.a]\ntokens = "tokens.css"\n')
    res, doc = run("profiles", "check", "a")
    assert res.exit_code == 0 and all(i["status"] == "ok" for i in doc["items"])


def test_profiles_commands_respect_the_sandbox(work, tmp_path):
    jail = work / "jail"
    jail.mkdir()
    res = runner.invoke(app, ["--allow", str(jail), "profiles", "save", "x", "--brand", "b", "--json"])
    assert res.exit_code == 3 and not (work / "mysuite.toml").exists()


# ---------------------------------------------------------------------------- pipeline
@need
def test_pipeline_runs_every_step_under_its_profile(work):
    from mysuite.pipeline import engine

    write_config(work, ACME)
    pipe = engine.parse({"profile": "acme", "inputs": ["logo.svg"], "step": [{"tool": "export"}]}, base=work)
    (res,) = engine.run(pipe)
    assert res.status == "ok" and any("mono-white" in o for o in res.outputs)                  # profile defaults reached the child
    with pytest.raises(engine.PipelineError):
        engine.parse({"profile": "acme", "step": []}, base=work)
