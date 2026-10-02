from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from mysuite.cli import app
from mysuite.metadata.metadata import DATA_MINING, _build_manifest
from tests.helpers import runner

need = pytest.mark.skipif(not all(shutil.which(t) for t in ("exiftool", "magick")), reason="needs exiftool/magick")


def run(*args, global_args=()):
    res = runner.invoke(app, [*global_args, *[str(a) for a in args], "--json"])
    return res, json.loads(res.stdout)


@pytest.fixture
def jpg(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    subprocess.run(["magick", "-size", "40x30", "xc:red", "p.jpg"], check=True)
    return tmp_path / "p.jpg"


def tags(path):
    return subprocess.run(["exiftool", "-s", "-XMP-plus:DataMining", "-XMP-xmpRights:all", "-n", str(path)], capture_output=True, text=True).stdout


def test_manifest_gets_the_c2pa_training_mining_assertion_only_when_asked():
    plain = _build_manifest(author="A", copyright_notice=None, generator="g")
    assert [a["label"] for a in plain["assertions"]] == ["stds.schema-org.CreativeWork"]
    m = _build_manifest(author="A", copyright_notice=None, generator="g", no_ai=True)
    a = next(x for x in m["assertions"] if x["label"] == "c2pa.training-mining")
    assert set(a["data"]["entries"]) == {"c2pa.ai_generative_training", "c2pa.ai_training", "c2pa.ai_inference", "c2pa.data_mining"}
    assert all(v == {"use": "notAllowed"} for v in a["data"]["entries"].values())


@need
@pytest.mark.parametrize("policy,code", [("prohibited", "DMI-PROHIBITED"), ("prohibited-ai-training", "DMI-PROHIBITED-AIMLTRAINING"),
                                         ("prohibited-genai-training", "DMI-PROHIBITED-GENAIMLTRAINING")])
def test_declare_writes_the_plus_vocabulary_and_rights(jpg, tmp_path, policy, code):
    res, doc = run("metadata", "declare", jpg, "--policy", policy, "--owner", "Acme", "--terms-url", "https://acme.example/ai", "--overwrite")
    assert res.exit_code == 0, doc
    out = tmp_path / "p_declared.jpg"
    t = tags(out)
    assert f"DataMining                      : {code}" in t and "Marked                          : 1" in t.replace("True", "1")
    assert "https://acme.example/ai" in t and "Acme" in subprocess.run(["exiftool", "-XMP-plus:CopyrightOwnerName", "-s3", str(out)], capture_output=True, text=True).stdout
    assert tags(jpg).strip() == ""                                                     # the original is untouched


@need
def test_declare_validation_dry_run_and_overwrite(jpg, tmp_path):
    assert run("metadata", "declare", jpg, "--policy", "everything")[0].exit_code == 1
    res, doc = run("metadata", "declare", jpg, "--dry-run")
    assert doc["items"][0]["status"] == "planned" and not (tmp_path / "p_declared.jpg").exists()
    run("metadata", "declare", jpg)
    again = run("metadata", "declare", jpg)[1]
    assert again["items"][0]["status"] == "skipped_existing"


@need
def test_declaration_survives_into_inspect(jpg):
    run("metadata", "declare", jpg, "--policy", "prohibited-genai-training")
    res = runner.invoke(app, ["inspect", "p_declared.jpg", "--json"])
    meta = json.loads(res.stdout)["items"][0]["metadata"]
    assert meta["ai_declaration"] == "DMI-PROHIBITED-GENAIMLTRAINING" and "AI" in meta["usage_terms"]
    clean = runner.invoke(app, ["inspect", "p.jpg", "--json"])
    assert json.loads(clean.stdout)["items"][0]["metadata"]["ai_declaration"] is None


@need
def test_profile_policy_with_declare_and_no_ai(jpg, tmp_path):
    (tmp_path / "mysuite.toml").write_text('[profiles.a]\n[profiles.a.metadata]\npolicy = ["strip", "declare"]\nauthor = "Acme"\nterms_url = "https://acme.example/t"\nno_ai = true\n')
    res, doc = run("metadata", "apply", jpg, global_args=["--profile", "a"])
    assert res.exit_code == 0, doc
    assert doc["items"][0]["output"].endswith("p_stripped_declared.jpg")
    assert "https://acme.example/t" in tags(tmp_path / "p_stripped_declared.jpg") and "DMI-PROHIBITED" in tags(tmp_path / "p_stripped_declared.jpg")


@pytest.mark.skipif(not all(shutil.which(t) for t in ("exiftool", "magick", "c2patool")), reason="needs c2patool")
def test_credit_no_ai_embeds_the_assertion_and_it_reads_back(jpg, tmp_path):
    res, doc = run("metadata", "credit", jpg, "--author", "Acme", "--no-ai")
    assert res.exit_code == 0, doc
    manifest = subprocess.run(["c2patool", str(tmp_path / "p_credited.jpg")], capture_output=True, text=True).stdout
    assert "c2pa.training-mining" in manifest and "notAllowed" in manifest


def test_profile_validation_for_the_new_keys():
    from mysuite import profiles as P

    assert P.parse_profile("x", {"metadata": {"policy": ["declare"], "no_ai": True, "terms_url": "u"}}).policy == ["declare"]
    with pytest.raises(P.ProfileError, match="no_ai"):
        P.parse_profile("x", {"metadata": {"no_ai": "yes"}})


def test_vocabulary_is_complete():
    assert set(DATA_MINING) == {"prohibited", "prohibited-ai-training", "prohibited-genai-training"}
