"""WS-D: metadata strip keeps the colour profile, can keep the credit, skips its own outputs, and shares the PDF engine."""
from __future__ import annotations

import io
import json
import shutil
import subprocess

import pytest
from PIL import Image, ImageCms
from pypdf import PdfReader

from mysuite.cli import app
from tests.helpers import runner

pytestmark = pytest.mark.skipif(shutil.which("exiftool") is None or shutil.which("magick") is None,
                                reason="requires exiftool and magick")


def tags(path, *names) -> dict:
    out = subprocess.run(["exiftool", "-j", "-a", "-G1", "-s", *names, str(path)], capture_output=True, text=True).stdout
    return json.loads(out)[0]


def run(*args):
    res = runner.invoke(app, [*[str(a) for a in args], "--json"])
    try:
        return res, json.loads(res.stdout)
    except json.JSONDecodeError:
        return res, None


PRIVATE = ["-Make=Apple", "-Model=iPhone 15 Pro", "-SerialNumber=C02XYZ123", "-Software=Adobe Photoshop 27.0",
           "-LensSerialNumber=LS999", "-GPSLatitude=52.52", "-GPSLatitudeRef=N", "-GPSLongitude=13.405", "-GPSLongitudeRef=E",
           "-UserComment=secret host", "-XMP-xmp:CreatorTool=Photoshop", "-IPTC:City=Berlin"]
CREDIT = ["-Artist=Jane Doe", "-Copyright=© 2026 Jane Doe", "-XMP-dc:Creator=Jane Doe", "-XMP-dc:Rights=© 2026 Jane Doe",
          "-IPTC:By-line=Jane Doe", "-IPTC:Credit=Acme News", "-IPTC:CopyrightNotice=© 2026 Jane Doe",
          "-XMP-photoshop:Credit=Acme News", "-XMP-photoshop:Source=Acme Archive"]


def seeded(path, fmt="JPEG"):
    """A picture with an embedded Display-P3-ish ICC profile, private data and a credit."""
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    Image.new("RGB", (120, 90), (40, 120, 200)).save(path, format=fmt, icc_profile=profile)
    subprocess.run(["exiftool", "-q", "-overwrite_original", "-charset", "iptc=UTF8", *PRIVATE, *CREDIT, str(path)], check=True)
    return path


@pytest.mark.parametrize("name,fmt", [("photo.jpg", "JPEG"), ("photo.tif", "TIFF"), ("photo.png", "PNG")])
def test_strip_keeps_the_colour_profile_and_drops_gps(tmp_path, name, fmt):
    src = seeded(tmp_path / name, fmt)
    res, doc = run("metadata", "strip", src)
    assert res.exit_code == 0, res.output
    out = doc["items"][0]["output"]
    t = tags(out)
    assert any(k.startswith("ICC") for k in t), t                  # colours still look right
    assert not [k for k in t if "GPS" in k or k.endswith(":Make") or "Serial" in k or k.endswith(":Artist")], t


def test_strip_drop_icc_removes_the_profile_too(tmp_path):
    src = seeded(tmp_path / "photo.jpg")
    res, doc = run("metadata", "strip", src, "--drop-icc")
    assert res.exit_code == 0, res.output
    assert not [k for k in tags(doc["items"][0]["output"]) if k.startswith("ICC")]


def test_strip_keep_credit_drops_private_data_and_keeps_the_credit(tmp_path):
    src = seeded(tmp_path / "photo.jpg")
    res, doc = run("metadata", "strip", src, "--keep-credit")
    assert res.exit_code == 0, res.output
    t = tags(doc["items"][0]["output"])
    for gone in ("GPS", "Make", "Model", "SerialNumber", "Software", "UserComment", "CreatorTool", "City"):
        assert not [k for k in t if ":" in k and k.split(":")[1].startswith(gone)], (gone, t)
    assert t["IFD0:Artist"] == "Jane Doe" and t["IFD0:Copyright"] == "© 2026 Jane Doe"
    assert t["XMP-dc:Creator"] == "Jane Doe" and t["XMP-dc:Rights"] == "© 2026 Jane Doe"
    assert t["IPTC:By-line"] == "Jane Doe" and t["IPTC:Credit"] == "Acme News"
    assert t["XMP-photoshop:Credit"] == "Acme News" and t["XMP-photoshop:Source"] == "Acme Archive"
    assert any(k.startswith("ICC") for k in t)


def test_a_folder_run_skips_mysuites_own_outputs(tmp_path):
    seeded(tmp_path / "photo.jpg")
    for suffix in ("stripped", "marked", "declared", "credited", "protected"):
        shutil.copyfile(tmp_path / "photo.jpg", tmp_path / f"photo_{suffix}.jpg")
    (tmp_path / "photo_stripped.jpg").unlink()
    res, doc = run("metadata", "strip", tmp_path)
    assert res.exit_code == 0, res.output
    assert [i["input"].rsplit("/", 1)[1] for i in doc["items"]] == ["photo.jpg"]
    assert any("mysuite wrote" in w for w in doc["warnings"])
    assert not (tmp_path / "photo_marked_stripped.jpg").exists()


def test_a_file_named_explicitly_is_processed_even_if_it_looks_like_an_output(tmp_path):
    src = seeded(tmp_path / "photo_marked.jpg")
    res, doc = run("metadata", "strip", src)
    assert res.exit_code == 0 and doc["items"][0]["status"] == "written"


def test_strip_on_a_pdf_uses_the_pdf_engine(tmp_path):
    buf = io.BytesIO()
    Image.new("RGB", (100, 100), "white").save(buf, format="PDF", title="Secret Title", author="Frank")
    src = tmp_path / "doc.pdf"
    src.write_bytes(buf.getvalue())
    res, doc = run("metadata", "strip", src)
    assert res.exit_code == 0, res.output
    out = doc["items"][0]["output"]
    assert out.endswith("doc_stripped.pdf")
    raw = open(out, "rb").read()
    assert b"Secret Title" not in raw and b"Frank" not in raw              # not an incremental update that hides it
    assert PdfReader(out, strict=True).metadata is None


# ------------------------------------------------------------------ 3. credit: IPTC Core always (exiftool); C2PA only with c2patool
def plain(path, fmt="JPEG"):
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    Image.new("RGB", (120, 90), (40, 120, 200)).save(path, format=fmt, icc_profile=profile)
    return path


CREDIT_ARGS = ["--author", "Jane Doe", "--copyright", "© 2026 Jane Doe", "--credit-line", "Jane Doe / Acme News",
               "--source", "Acme Archive", "--usage-terms", "Editorial use only"]


@pytest.mark.parametrize("name,fmt", [("p.jpg", "JPEG"), ("p.tif", "TIFF")])
def test_credit_writes_iptc_core_with_exiftool(tmp_path, monkeypatch, name, fmt):
    monkeypatch.setenv("PATH", "/usr/bin:/bin:/opt/homebrew/bin")          # whatever is installed, the IPTC part is always there
    src = plain(tmp_path / name, fmt)
    res, doc = run("metadata", "credit", src, *CREDIT_ARGS)
    assert res.exit_code == 0, res.output
    t = tags(doc["items"][0]["output"])
    assert t["IPTC:By-line"] == "Jane Doe" and t["XMP-dc:Creator"] == "Jane Doe" and t["IFD0:Artist"] == "Jane Doe"
    assert t["IPTC:CopyrightNotice"] == "© 2026 Jane Doe" and t["XMP-dc:Rights"] == "© 2026 Jane Doe"
    assert t["IPTC:Credit"] == "Jane Doe / Acme News" and t["XMP-photoshop:Credit"] == "Jane Doe / Acme News"
    assert t["IPTC:Source"] == "Acme Archive" and t["XMP-photoshop:Source"] == "Acme Archive"
    assert t["XMP-xmpRights:UsageTerms"] == "Editorial use only"
    assert any(k.startswith("ICC") for k in t)


def test_credit_png_gets_the_xmp_and_exif_fields(tmp_path):
    src = plain(tmp_path / "p.png", "PNG")
    res, doc = run("metadata", "credit", src, *CREDIT_ARGS)
    assert res.exit_code == 0, res.output
    t = tags(doc["items"][0]["output"])
    assert t["XMP-dc:Creator"] == "Jane Doe" and t["XMP-photoshop:Credit"] == "Jane Doe / Acme News"


def test_credit_without_c2patool_says_c2pa_was_not_added(tmp_path, monkeypatch):
    from mysuite.metadata import metadata

    monkeypatch.setattr(metadata, "c2pa_tool", lambda tools: None)
    src = plain(tmp_path / "p.jpg")
    res, doc = run("metadata", "credit", src, "--author", "Jane Doe")
    assert res.exit_code == 0, res.output
    item = doc["items"][0]
    assert item["status"] == "written" and item["c2pa"] == "not added: c2patool is not installed"
    assert any("c2patool" in w for w in doc["warnings"])


def test_credit_with_c2patool_signs_last_and_labels_the_test_certificate(tmp_path, monkeypatch):
    """c2patool isn't installed here: a stand-in records the call and copies the file. UNVERIFIED against real c2patool."""
    from mysuite.metadata import metadata

    calls = []

    def fake_run(cmd, **kw):
        if cmd[0] == "fake-c2patool":
            calls.append(tags(cmd[1]))                                   # the file as it is when it gets signed
            manifest = json.loads(open(cmd[cmd.index("-m") + 1]).read())
            assert manifest["assertions"][0]["data"]["author"][0]["name"] == "Jane Doe"
            shutil.copyfile(cmd[1], cmd[cmd.index("-o") + 1])
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return real_run(cmd, **kw)

    real_run = metadata.run
    monkeypatch.setattr(metadata, "run", fake_run)
    monkeypatch.setattr(metadata, "c2pa_tool", lambda tools: "fake-c2patool")
    src = plain(tmp_path / "p.jpg")
    res, doc = run("metadata", "credit", src, "--author", "Jane Doe", "--no-ai")
    assert res.exit_code == 0, res.output
    assert len(calls) == 1
    assert calls[0]["IPTC:By-line"] == "Jane Doe"            # IPTC written BEFORE signing, so the manifest covers it
    assert "test certificate" in doc["items"][0]["c2pa"]
    assert tags(doc["items"][0]["output"])["XMP-plus:DataMining"].startswith("Prohibited")   # --no-ai also as XMP


def test_credit_leaves_no_temporary_files(tmp_path):
    src = plain(tmp_path / "p.jpg")
    run("metadata", "credit", src, "--author", "Jane Doe")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["p.jpg", "p_credited.jpg"]


# ------------------------------------------------------------------ declare warns that GPS is kept
def test_declare_warns_that_the_location_is_still_in_the_file(tmp_path):
    src = seeded(tmp_path / "photo.jpg")
    res, doc = run("metadata", "declare", src, "--owner", "Jane Doe")
    assert res.exit_code == 0, res.output
    assert any("GPS" in w and "metadata protect" in w for w in doc["warnings"])
    assert "GPS:GPSLatitude" in tags(doc["items"][0]["output"])        # declare changes nothing else: it is still there


def test_declare_on_a_file_without_gps_has_no_warning(tmp_path):
    src = plain(tmp_path / "p.jpg")
    res, doc = run("metadata", "declare", src)
    assert res.exit_code == 0 and not doc["warnings"]


# ------------------------------------------------------------------ protect & credit in one go: one output, no leftovers
def big_seeded(path):
    """A photo large enough to carry a mark, with private data, a stale credit and a colour profile."""
    import numpy as np

    rng = np.random.default_rng(1)
    arr = np.clip(rng.normal(128, 40, (320, 400, 3)), 0, 255).astype("uint8")
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    Image.fromarray(arr).save(path, quality=95, icc_profile=profile)
    subprocess.run(["exiftool", "-q", "-overwrite_original", "-charset", "iptc=UTF8", *PRIVATE, str(path)], check=True)
    return path


def test_protect_strips_private_data_credits_and_declares_into_one_file(tmp_path):
    src = big_seeded(tmp_path / "photo.jpg")
    res, doc = run("metadata", "protect", src, "--author", "Jane Doe", "--copyright", "© 2026 Jane Doe",
                   "--credit-line", "Jane Doe / Acme News")
    assert res.exit_code == 0, res.output
    item = doc["items"][0]
    assert item["output"].endswith("photo_protected.jpg") and item["steps"] == ["strip-private", "credit", "declare"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["photo.jpg", "photo_protected.jpg"]       # nothing left over
    t = tags(item["output"])
    assert not [k for k in t if "GPS" in k or k.endswith(":Make") or "Serial" in k or k.endswith("UserComment")]
    assert t["IPTC:By-line"] == "Jane Doe" and t["XMP-dc:Rights"] == "© 2026 Jane Doe" and t["IPTC:Credit"] == "Jane Doe / Acme News"
    assert t["XMP-plus:DataMining"].startswith("Prohibited")
    assert any(k.startswith("ICC") for k in t)
    assert item["c2pa"]                                                # says whether C2PA was added


def test_protect_with_a_mark_key_marks_too_and_keeps_the_credit(tmp_path, monkeypatch):
    from mysuite.mark import core as M

    monkeypatch.setenv("MYSUITE_MARK_KEY", "correct horse battery")
    src = big_seeded(tmp_path / "photo.jpg")
    res, doc = run("metadata", "protect", src, "--author", "Jane Doe", "--mark")
    assert res.exit_code == 0, res.output
    item = doc["items"][0]
    assert item["steps"] == ["strip-private", "mark", "credit", "declare"]
    assert M.detect_file(item["output"], key="correct horse battery").detected
    assert tags(item["output"])["IPTC:By-line"] == "Jane Doe"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["photo.jpg", "photo_protected.jpg"]


def test_protect_mark_without_a_key_is_refused_before_anything_runs(tmp_path, monkeypatch):
    monkeypatch.delenv("MYSUITE_MARK_KEY", raising=False)
    src = big_seeded(tmp_path / "photo.jpg")
    res, doc = run("metadata", "protect", src, "--author", "Jane Doe", "--mark")
    assert res.exit_code != 0 and not (tmp_path / "photo_protected.jpg").exists()


def test_protect_dry_run(tmp_path):
    src = big_seeded(tmp_path / "photo.jpg")
    res, doc = run("metadata", "protect", src, "--author", "Jane Doe", "--dry-run")
    assert res.exit_code == 0 and doc["items"][0]["status"] == "planned"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["photo.jpg"]


def test_apply_writes_only_the_final_file(tmp_path):
    src = big_seeded(tmp_path / "photo.jpg")
    res, doc = run("metadata", "apply", src, "--policy", "strip,declare", "--author", "Jane Doe")
    assert res.exit_code == 0, res.output
    assert sorted(p.name for p in tmp_path.iterdir()) == ["photo.jpg", "photo_stripped_declared.jpg"]


def test_apply_protect_policy_name(tmp_path):
    src = big_seeded(tmp_path / "photo.jpg")
    res, doc = run("metadata", "apply", src, "--policy", "protect", "--author", "Jane Doe")
    assert res.exit_code == 0, res.output
    assert doc["items"][0]["output"].endswith("photo_protected.jpg")


def test_policies_known_to_profiles():
    from mysuite.profiles import POLICIES

    assert {"strip", "strip-private", "credit", "declare", "mark", "protect", "randomize"} <= set(POLICIES)
