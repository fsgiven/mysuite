from __future__ import annotations

import os
from pathlib import Path

# Maps a recognized source file extension to a canonical format name.
SOURCE_EXTENSIONS: dict[str, str] = {
    ".svg": "svg",
    ".pdf": "pdf",
    ".eps": "eps",
    ".png": "png",
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".webp": "webp",
    ".tif": "tiff",
    ".tiff": "tiff",
    ".bmp": "bmp",
    ".gif": "gif",
}

# Target formats convert can write to, and their output extension. Deliberately
# excludes svg (vectorization is out of scope) and icns (Export's job — a fixed
# 7-size iconutil bundle, not a one-file-in/one-file-out conversion). ico is
# allowed as a single-image raster write only, not Export's multi-frame bundle.
TARGET_EXTENSIONS: dict[str, str] = {
    "pdf": ".pdf",
    "eps": ".eps",
    "png": ".png",
    "jpeg": ".jpg",
    "webp": ".webp",
    "tiff": ".tiff",
    "bmp": ".bmp",
    "gif": ".gif",
    "ico": ".ico",
}

RASTER_FORMATS = {"png", "jpeg", "webp", "tiff", "bmp", "gif"}


class InvalidInputError(ValueError):
    pass


def detect_source_format(path: Path) -> str | None:
    """Returns the canonical source format name for path's extension, or None
    if it isn't a recognized convertible format."""
    return SOURCE_EXTENSIONS.get(path.suffix.lower())


def output_path_for(input_path: Path, target_format: str) -> Path:
    """The output path for converting input_path to target_format: same folder
    and stem as the source, new extension — "duplicate and convert" in place."""
    return input_path.with_suffix(TARGET_EXTENSIONS[target_format])



def _absolute(path: Path) -> Path:
    """Absolute (but symlink-preserving) path. Every downstream tool gets a path
    that starts with "/" — a file named "-all=.jpg" or "-@" found by globbing a
    folder would otherwise be parsed by exiftool/ImageMagick as an option, which
    a hostile folder could abuse. Not resolve(): that would write outputs next
    to a symlink's target instead of beside the link."""
    return Path(os.path.abspath(path))


def resolve_input_files(inputs: list[Path], *, recursive: bool = False) -> list[Path]:
    """Expands a mix of file paths and directories into a flat, de-duplicated,
    order-preserving list of recognized-format files. A directory is globbed
    for any recognized source extension; an explicitly-named file with an
    unrecognized extension raises immediately rather than being silently
    skipped."""
    resolved: list[Path] = []
    seen: set[Path] = set()
    for item in inputs:
        if item.is_dir():
            pattern = "**/*" if recursive else "*"
            found = sorted(
                f for f in item.glob(pattern)
                if f.is_file() and f.suffix.lower() in SOURCE_EXTENSIONS
            )
        elif item.is_file():
            if item.suffix.lower() not in SOURCE_EXTENSIONS:
                raise InvalidInputError(
                    f"unrecognized source format: {item} (suffix {item.suffix!r})"
                )
            found = [item]
        else:
            raise InvalidInputError(f"not a file or directory: {item}")
        for f in found:
            key = f.resolve()
            if key not in seen:
                seen.add(key)
                resolved.append(_absolute(f))

    if not resolved:
        raise InvalidInputError("no convertible files found")

    return resolved


def check_no_output_collisions(files: list[Path], target_format: str) -> None:
    """Raises if two different input files would produce the same output path
    for target_format — e.g. logo.png and logo.jpg both converted --to webp
    would both want logo.webp. Checked once up front, before any conversion
    starts, so a batch never partially clobbers itself."""
    by_output: dict[Path, list[Path]] = {}
    for f in files:
        key = output_path_for(f, target_format).resolve()
        by_output.setdefault(key, []).append(f)

    collisions = {out: srcs for out, srcs in by_output.items() if len(srcs) > 1}
    if collisions:
        details = "; ".join(
            f"{out} <- {', '.join(str(s) for s in srcs)}" for out, srcs in collisions.items()
        )
        raise InvalidInputError(
            f"multiple input files would produce the same output file: {details} — "
            "convert them in separate runs or rename one first"
        )
