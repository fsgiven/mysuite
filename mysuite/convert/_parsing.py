from __future__ import annotations

from mysuite import sandbox

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

# Spellings people type for a target, mapped to the canonical name (`--to jpg` is `--to jpeg`).
TARGET_ALIASES: dict[str, str] = {"jpg": "jpeg", "tif": "tiff"}

RASTER_FORMATS = {"png", "jpeg", "webp", "tiff", "bmp", "gif"}


class InvalidInputError(ValueError):
    pass


def normalize_target(name: str) -> str:
    """`JPG` -> `jpeg`, `tif` -> `tiff`; other names are only lower-cased."""
    name = name.strip().lower()
    return TARGET_ALIASES.get(name, name)


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

    sandbox.check_inputs(resolved)
    return resolved


def output_paths_for(files: list[Path], target_format: str) -> dict[Path, Path]:
    """Output path per input. Two inputs that would share an output (logo.png and logo.jpg both --to webp)
    each get their source extension in the name instead (logo_png.webp, logo_jpg.webp): the batch goes on."""
    by_output: dict[Path, list[Path]] = {}
    for f in files:
        by_output.setdefault(output_path_for(f, target_format).resolve(), []).append(f)
    out: dict[Path, Path] = {}
    for group in by_output.values():
        for f in group:
            if len(group) == 1:
                out[f] = output_path_for(f, target_format)
            else:
                tag = f.suffix.lower().lstrip(".") or "file"
                out[f] = f.with_name(f"{f.stem}_{tag}{TARGET_EXTENSIONS[target_format]}")
    return out
