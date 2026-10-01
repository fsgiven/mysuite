from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from mysuite.config import ToolPaths
from mysuite.convert import _parsing as convert_parsing
from mysuite.export import _parsing as export_parsing
from mysuite.metadata.metadata import strip_file

_HOSTILE = ["-all=.png", "-overwrite_original.png", "-@.png"]


@pytest.fixture
def hostile_dir(tmp_path):
    for name in _HOSTILE:
        (tmp_path / name).write_bytes(b"\x89PNG\r\n\x1a\n")
    return tmp_path


def test_convert_resolver_never_returns_a_path_starting_with_a_dash(hostile_dir, monkeypatch):
    monkeypatch.chdir(hostile_dir)
    files = convert_parsing.resolve_input_files([hostile_dir.__class__(".")])
    assert len(files) == len(_HOSTILE)
    assert all(f.is_absolute() and not f.name.startswith("/") and str(f).startswith("/") for f in files)


def test_export_resolver_never_returns_a_path_starting_with_a_dash(tmp_path, monkeypatch):
    (tmp_path / "-all=.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    monkeypatch.chdir(tmp_path)
    files = export_parsing.resolve_input_files([tmp_path.__class__(".")])
    assert [str(f).startswith("/") for f in files] == [True]


def test_resolver_keeps_symlinks_so_output_lands_beside_the_link(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (real / "a.png").write_bytes(b"\x89PNG")
    link = tmp_path / "link"
    os.symlink(real, link)
    (files,) = convert_parsing.resolve_input_files([link / "a.png"])
    assert files.parent == link


@pytest.mark.slow
@pytest.mark.skipif(shutil.which("exiftool") is None or shutil.which("magick") is None, reason="needs exiftool+magick")
def test_strip_treats_a_dash_named_file_as_a_file_not_an_exiftool_option(tmp_path, monkeypatch):
    path = tmp_path / "-overwrite_original.png"
    subprocess.run(["magick", "-size", "20x20", "xc:red", str(path)], check=True)
    monkeypatch.chdir(tmp_path)
    (found,) = convert_parsing.resolve_input_files([tmp_path.__class__(".")])
    out = strip_file(found, tools=ToolPaths()).output_path
    assert out.exists()
    assert subprocess.run(["magick", "identify", str(out)], capture_output=True).returncode == 0
