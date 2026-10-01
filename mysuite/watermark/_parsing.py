from __future__ import annotations

from pathlib import Path

from mysuite.convert._parsing import RASTER_FORMATS


def output_path_for(input_path: Path, source_format: str) -> Path:
    """The output path for a watermarked copy of input_path: same folder as
    the source, with a "_watermarked" suffix. Raster sources keep their
    original extension (compositing a stamp doesn't need a format change);
    vector sources (svg/pdf/eps) become .png since watermarking is a raster
    compositing operation — same "rasterize the vector, keep raster as-is"
    split Cutout already uses."""
    ext = input_path.suffix if source_format in RASTER_FORMATS else ".png"
    return input_path.with_name(f"{input_path.stem}_watermarked{ext}")
