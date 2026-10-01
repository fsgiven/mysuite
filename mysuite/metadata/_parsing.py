from __future__ import annotations

from pathlib import Path


def output_path_for(input_path: Path, mode: str) -> Path:
    """The output path for a metadata operation on input_path: same folder,
    same extension (neither strip nor credit change the image format), with
    a suffix naming the mode — photo.jpg -> photo_stripped.jpg or
    photo_credited.jpg — so it never collides with the source."""
    return input_path.with_name(f"{input_path.stem}_{mode}{input_path.suffix}")
