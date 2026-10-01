from __future__ import annotations

import random
import shutil
import subprocess

import pytest

from mysuite.config import ToolPaths
from mysuite.metadata.metadata import randomize_file, strip_file

pytestmark = pytest.mark.slow

skip_if_tools_missing = pytest.mark.skipif(
    shutil.which("exiftool") is None or shutil.which("magick") is None,
    reason="requires exiftool and magick",
)

_IDENTIFYING = ("frank", "macbook", "photoshop", "berlin", "secret", "exiftool", "c02xyz")


def _exif(path, *args):
    return subprocess.run(["exiftool", *args, str(path)], capture_output=True, text=True).stdout


def _raw_strings(path) -> str:
    return subprocess.run(["strings", "-a", str(path)], capture_output=True, text=True).stdout.lower()


def _seed(path):
    subprocess.run(["magick", "-size", "120x90", "gradient:#3388ff-#ff6633", "-set", "comment", "Franks-MacBook-Pro", str(path)], check=True)
    subprocess.run(
        [
            "exiftool", "-q", "-overwrite_original",
            "-Make=Apple", "-Model=MacBook Pro Camera", "-Software=Adobe Photoshop 27.0",
            "-SerialNumber=C02XYZ123", "-Artist=Frank", "-Copyright=(c) Frank",
            "-GPSLatitude=52.52", "-GPSLatitudeRef=N", "-GPSLongitude=13.405", "-GPSLongitudeRef=E",
            "-IPTC:City=Berlin", "-XMP-dc:Creator=Frank", "-XMP-xmp:CreatorTool=Photoshop",
            "-UserComment=secret host", str(path),
        ],
        check=True,
    )


@pytest.fixture
def seeded_jpg(tmp_path):
    path = tmp_path / "seed.jpg"
    _seed(path)
    return path


@skip_if_tools_missing
def test_strip_leaves_no_identifying_metadata_or_strings(seeded_jpg):
    out = strip_file(seeded_jpg, tools=ToolPaths()).output_path
    assert _exif(out, "-a", "-s", "-EXIF:all", "-XMP:all", "-IPTC:all", "-GPS:all", "-ICC_Profile:all").strip() == ""
    raw = _raw_strings(out)
    assert not [w for w in _IDENTIFYING if w in raw]


@skip_if_tools_missing
def test_strip_keeps_orientation_so_photos_dont_come_out_sideways(tmp_path):
    path = tmp_path / "rot.jpg"
    subprocess.run(["magick", "-size", "90x30", "gradient:red-blue", str(path)], check=True)
    subprocess.run(["exiftool", "-q", "-overwrite_original", "-Orientation#=6", "-Make=Apple", str(path)], check=True)
    out = strip_file(path, tools=ToolPaths()).output_path
    assert _exif(out, "-s3", "-Orientation#").strip() == "6"
    assert _exif(out, "-s3", "-Make").strip() == ""


@skip_if_tools_missing
def test_strip_removes_tiff_ifd0_tags_exiftool_cannot_delete(tmp_path):
    path = tmp_path / "seed.tiff"
    subprocess.run(["magick", "-size", "60x40", "gradient:red-blue", str(path)], check=True)
    subprocess.run(
        ["exiftool", "-q", "-overwrite_original", "-Artist=Frank", "-Copyright=(c) Frank",
         "-Software=Adobe Photoshop 27", "-ImageDescription=private", str(path)],
        check=True,
    )
    out = strip_file(path, tools=ToolPaths()).output_path
    assert _exif(out, "-a", "-s", "-Artist", "-Copyright", "-Software", "-ImageDescription").strip() == ""
    assert not [w for w in _IDENTIFYING if w in _raw_strings(out)]


@skip_if_tools_missing
def test_randomize_writes_one_coherent_profile_and_no_leaks(seeded_jpg):
    out = randomize_file(seeded_jpg, tools=ToolPaths(), rng=random.Random(1)).output_path
    tags = {
        k.strip(): v for k, v in (
            line.split(":", 1) for line in _exif(out, "-s", "-Make", "-Model", "-LensModel", "-ISO").splitlines()
        )
    }
    assert tags["Make"].strip() == "NIKON CORPORATION" and tags["Model"].strip() == "NIKON D850"
    assert "NIKKOR" in tags["LensModel"]
    assert _exif(out, "-a", "-s", "-XMP:all", "-IPTC:all", "-GPS:all", "-Artist", "-Copyright").strip() == ""
    assert not [w for w in _IDENTIFYING if w in _raw_strings(out)]


@skip_if_tools_missing
def test_randomize_never_mutates_source_and_is_valid_image(seeded_jpg):
    before = seeded_jpg.read_bytes()
    out = randomize_file(seeded_jpg, tools=ToolPaths(), rng=random.Random(2)).output_path
    assert seeded_jpg.read_bytes() == before
    assert subprocess.run(["magick", "identify", str(out)], capture_output=True).returncode == 0


@skip_if_tools_missing
def test_skips_existing_without_overwrite(seeded_jpg):
    first = randomize_file(seeded_jpg, tools=ToolPaths())
    assert first.status == "written"
    assert randomize_file(seeded_jpg, tools=ToolPaths()).status == "skipped_existing"
