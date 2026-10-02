"""Prove an optimised SVG still looks the same: render both with rsvg-convert and compare the pixels."""
from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

from mysuite.config import ToolPaths
from mysuite.utils.subprocess_utils import MysuiteToolError, run

MAX_MEAN_DIFF = 0.6        # of 255, averaged over every channel of every pixel
MAX_EDGE_DIFF = 40         # the 99.9th-percentile pixel may differ by at most this much (anti-aliasing at rounded edges)


def _render(svg_text: str, tools: ToolPaths, width: int, workdir: Path, name: str) -> np.ndarray:
    src = workdir / f"{name}.svg"
    src.write_text(svg_text, encoding="utf-8")
    png = workdir / f"{name}.png"
    run([tools.rsvg_convert, "-w", str(width), "-f", "png", "-o", str(png), str(src)])
    with Image.open(png) as image:
        return np.asarray(image.convert("RGBA"), dtype=np.int16)


def renders_same(before: str, after: str, tools: ToolPaths, *, width: int = 384) -> tuple[bool, float, int]:
    """(same?, mean difference, 99.9th-percentile difference). Raises MysuiteToolError if either cannot be rendered."""
    with tempfile.TemporaryDirectory(prefix="mysuite-verify-") as tmp:
        work = Path(tmp)
        a = _render(before, tools, width, work, "a")
        b = _render(after, tools, width, work, "b")
    if a.shape != b.shape:
        return False, 255.0, 255
    diff = np.abs(a - b)
    mean = float(diff.mean())
    edge = int(np.percentile(diff, 99.9))
    return mean <= MAX_MEAN_DIFF and edge <= MAX_EDGE_DIFF, round(mean, 3), edge
