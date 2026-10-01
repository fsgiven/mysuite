from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from mysuite.export.units import InvalidSizeError, Size, parse_size

VALID_FORMATS = {"png", "pdf", "eps", "svg", "jpeg", "webp", "tiff", "ico", "icns"}
VALID_PROFILES = {"rgb", "cmyk"}


class InvalidInputError(ValueError):
    pass



def _absolute(path: Path) -> Path:
    """Absolute (but symlink-preserving) path. Every downstream tool gets a path
    that starts with "/" — a file named "-all=.jpg" or "-@" found by globbing a
    folder would otherwise be parsed by exiftool/ImageMagick as an option, which
    a hostile folder could abuse. Not resolve(): that would write outputs next
    to a symlink's target instead of beside the link."""
    return Path(os.path.abspath(path))


def resolve_input_files(inputs: list[Path], *, recursive: bool = False) -> list[Path]:
    """Expands a mix of SVG file paths and directories (globbed for *.svg) into a
    flat, de-duplicated, order-preserving list of files. Raises InvalidInputError
    if nothing resolves, or if two resolved files would produce the same output
    name (same stem) — which would otherwise silently clobber each other."""
    resolved: list[Path] = []
    seen_paths: set[Path] = set()
    for item in inputs:
        if item.is_dir():
            pattern = "**/*.svg" if recursive else "*.svg"
            found = sorted(item.glob(pattern))
        elif item.is_file():
            found = [item]
        else:
            raise InvalidInputError(f"not a file or directory: {item}")
        for f in found:
            key = f.resolve()
            if key not in seen_paths:
                seen_paths.add(key)
                resolved.append(_absolute(f))

    if not resolved:
        raise InvalidInputError("no SVG files found")

    by_stem: dict[str, list[Path]] = {}
    for f in resolved:
        by_stem.setdefault(f.stem, []).append(f)
    duplicates = {stem: files for stem, files in by_stem.items() if len(files) > 1}
    if duplicates:
        details = "; ".join(
            f"{stem!r} from {', '.join(str(f) for f in files)}" for stem, files in duplicates.items()
        )
        raise InvalidInputError(
            f"multiple input files would produce the same output name: {details} — "
            "rename one or process them in separate runs"
        )

    return resolved


def parse_csv(value: Optional[str]) -> Optional[list[str]]:
    if value is None:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_sizes(value: Optional[str], *, dpi: float, default_unit: str = "px") -> Optional[list[Size]]:
    parsed = parse_csv(value)
    if parsed is None:
        return None
    try:
        return [parse_size(item, dpi=dpi, default_unit=default_unit) for item in parsed]
    except InvalidSizeError as exc:
        raise ValueError(str(exc)) from exc
