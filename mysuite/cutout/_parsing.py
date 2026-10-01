from __future__ import annotations

from pathlib import Path


def output_path_for(input_path: Path) -> Path:
    """The output path for a cutout of input_path: same folder as the source,
    with a "_cutout" suffix and a forced .png extension (transparency needs an
    alpha-capable format) — e.g. photo.jpg -> photo_cutout.png. The suffix (not
    just a new extension) makes it unambiguous this is a subject-isolated
    copy, not a plain format conversion, and can never collide with an
    existing same-stem file of a different original format."""
    return input_path.with_name(f"{input_path.stem}_cutout.png")
