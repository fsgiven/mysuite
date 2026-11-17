from __future__ import annotations

from pathlib import Path

# Codec choice -> output extension. Each codec is a distinct, specialized
# encoder (matching what Squoosh.app itself offers), not a generic magick
# writer — oxipng and pngquant are both PNG targets but fundamentally
# different (lossless vs. lossy), so they're kept as separate codec choices
# rather than folded into one "png" option.
CODECS: dict[str, str] = {
    "mozjpeg": ".jpg",
    "webp": ".webp",
    "avif": ".avif",
    "oxipng": ".png",
    "pngquant": ".png",
    "gifsicle": ".gif",
}


class InvalidCompressInputError(ValueError):
    pass


class CompressError(RuntimeError):
    pass


def output_path_for(input_path: Path, codec: str) -> Path:
    """The output path for compressing input_path via codec: same folder,
    stem + "_compressed", codec's extension."""
    return input_path.with_name(f"{input_path.stem}_compressed{CODECS[codec]}")


def output_paths_for(files: list[Path], codec: str) -> dict[Path, Path]:
    """Output path per input. When two inputs would share one output (photo.png and photo.jpg both to
    --codec webp), each of them gets its source extension in the name instead (photo_png_compressed.webp,
    photo_jpg_compressed.webp), so a batch never aborts over a name and never clobbers itself."""
    by_output: dict[Path, list[Path]] = {}
    for f in files:
        by_output.setdefault(output_path_for(f, codec).resolve(), []).append(f)
    out: dict[Path, Path] = {}
    for group in by_output.values():
        for f in group:
            if len(group) == 1:
                out[f] = output_path_for(f, codec)
            else:
                tag = f.suffix.lower().lstrip(".") or "file"
                out[f] = f.with_name(f"{f.stem}_{tag}_compressed{CODECS[codec]}")
    return out
