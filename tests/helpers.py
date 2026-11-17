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


# ------------------------------------------------------------------ dashboard tests: wait for a condition, not a clock
async def wait_until(pilot, condition, *, timeout: float = 15.0, what: str = "the condition") -> None:
    """Pump the app until `condition()` is true. A fixed pause is too short on a slow CI runner."""
    import time

    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out after {timeout:g}s waiting for {what}")
        await pilot.pause(0.05)


async def settle(pilot) -> None:
    """Let a tool screen's debounced live preview run, then its plan/run workers finish.

    `workers.wait_for_complete()` alone returns at once if the debounce timer has not fired yet (no worker exists),
    and the test then reads the previous command or plan.
    """
    await pilot.pause()                                         # deliver pending Changed messages (they start the timer)
    await wait_until(pilot, lambda: getattr(pilot.app.screen, "_preview_timer", None) is None, what="the live preview")
    from textual.worker import WorkerCancelled

    for _ in range(5):                       # a plan worker superseded by a newer one is cancelled: wait for the newer one
        try:
            await pilot.app.workers.wait_for_complete()
            break
        except WorkerCancelled:
            await pilot.pause()
    await pilot.pause()
