from __future__ import annotations

from pathlib import Path

from mysuite.convert._parsing import InvalidInputError
from mysuite.convert._parsing import resolve_input_files as _resolve_any

# Photos only — enhance upscales/restores raster images. SVG/PDF/EPS (vector,
# resolution-independent) and GIF (possibly animated) are out of scope.
ENHANCE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
OUTPUT_EXTENSIONS = {"png": ".png", "jpg": ".jpg", "webp": ".webp"}


class InvalidEnhanceInputError(ValueError):
    pass


def output_path_for(input_path: Path, output_format: str) -> Path:
    """Beside the source: photo.jpg -> photo_enhanced.png. Writing next to the
    source (like every other mysuite tool) also means same-named files in
    different folders can never collide."""
    return input_path.with_name(f"{input_path.stem}_enhanced{OUTPUT_EXTENSIONS[output_format]}")


def resolve_input_files(inputs: list[Path], *, recursive: bool = False) -> list[Path]:
    """convert's resolver (absolute paths, de-duplication, folder globbing),
    narrowed to the formats enhance supports. An explicitly named file in an
    unsupported format raises instead of being silently skipped."""
    try:
        found = _resolve_any(inputs, recursive=recursive)
    except InvalidInputError as exc:
        raise InvalidEnhanceInputError(str(exc)) from exc

    explicit = {p.resolve() for p in inputs if p.is_file()}
    supported: list[Path] = []
    for f in found:
        if f.suffix.lower() in ENHANCE_EXTENSIONS:
            supported.append(f)
        elif f.resolve() in explicit:
            raise InvalidEnhanceInputError(
                f"unsupported format for enhance: {f.name} (supports {', '.join(sorted(ENHANCE_EXTENSIONS))})"
            )
    if not supported:
        raise InvalidEnhanceInputError("no enhanceable photos found")
    return supported


def check_no_output_collisions(files: list[Path], output_format: str) -> None:
    """photo.jpg and photo.png in one folder would both want photo_enhanced.png;
    checked once up front so a batch never partially clobbers itself."""
    by_output: dict[Path, list[Path]] = {}
    for f in files:
        by_output.setdefault(output_path_for(f, output_format).resolve(), []).append(f)
    clashes = {out: srcs for out, srcs in by_output.items() if len(srcs) > 1}
    if clashes:
        details = "; ".join(f"{out} <- {', '.join(str(s) for s in srcs)}" for out, srcs in clashes.items())
        raise InvalidEnhanceInputError(
            f"multiple input files would produce the same output file: {details} — "
            "enhance them in separate runs or rename one first"
        )
