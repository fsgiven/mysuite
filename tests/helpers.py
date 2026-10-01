from __future__ import annotations

import re
import subprocess
import zlib
from pathlib import Path

from typer.testing import CliRunner

from mysuite.cli import app

runner = CliRunner()

SIMPLE_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100" viewBox="0 0 200 100">'
    '<rect width="100" height="100" fill="#dd0000"/><rect x="100" width="100" height="100" fill="#0057b8"/></svg>'
)


def write_svg(path: Path, body: str = "", *, width=200, height=100, viewbox=True, extra_attrs="") -> Path:
    vb = f' viewBox="0 0 {width} {height}"' if viewbox else ""
    path.write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"{vb}{extra_attrs}>{body}</svg>'
    )
    return path


def cli(*args: str):
    return runner.invoke(app, [str(a) for a in args])


def identify(path: Path, fmt: str) -> str:
    return subprocess.run(["magick", "identify", "-format", fmt, str(path)], capture_output=True, text=True).stdout


def pdf_cmyk_operators(path: Path) -> list[tuple[float, float, float, float]]:
    """CMYK fill values (0-100%) actually stored in a PDF's content streams."""
    data = path.read_bytes()
    found: list[tuple[float, float, float, float]] = []
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", data, re.S):
        try:
            stream = zlib.decompress(m.group(1))
        except zlib.error:
            continue
        for op in re.findall(rb"([\d.]+) ([\d.]+) ([\d.]+) ([\d.]+) k\b", stream):
            found.append(tuple(round(float(x) * 100, 1) for x in op))  # type: ignore[arg-type]
    return found


def pdf_has_rgb_operators(path: Path) -> bool:
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", path.read_bytes(), re.S):
        try:
            stream = zlib.decompress(m.group(1))
        except zlib.error:
            continue
        if re.search(rb"[\d.]+ [\d.]+ [\d.]+ rg\b", stream):
            return True
    return False


def tiff_cmyk_pixel(path: Path, x: int, y: int) -> tuple[int, ...]:
    out = subprocess.run(
        ["magick", str(path), "-colorspace", "CMYK", "-depth", "8", "-format",
         f"%[fx:round(255*p{{{x},{y}}}.r)] %[fx:round(255*p{{{x},{y}}}.g)] %[fx:round(255*p{{{x},{y}}}.b)]", "info:"],
        capture_output=True, text=True,
    ).stdout.split()
    return tuple(int(v) for v in out)


def pixel_rgb(path: Path, x: int, y: int) -> tuple[int, int, int]:
    out = subprocess.run(
        ["magick", str(path), "-format", f"%[fx:round(255*p{{{x},{y}}}.r)],%[fx:round(255*p{{{x},{y}}}.g)],%[fx:round(255*p{{{x},{y}}}.b)]", "info:"],
        capture_output=True, text=True,
    ).stdout
    r, g, b = (int(v) for v in out.split(","))
    return r, g, b
