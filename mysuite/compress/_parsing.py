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


def output_path_for(input_path: Path, codec: str) -> Path:
    """The output path for compressing input_path via codec: same folder,
    stem + "_compressed", codec's extension."""
    return input_path.with_name(f"{input_path.stem}_compressed{CODECS[codec]}")


def check_no_output_collisions(files: list[Path], codec: str) -> None:
    """Raises if two different input files would produce the same output path
    for codec — e.g. photo.png and photo.jpg both compressed --codec webp
    would both want photo_compressed.webp. Checked once up front, before any
    compression starts, so a batch never partially clobbers itself."""
    by_output: dict[Path, list[Path]] = {}
    for f in files:
        key = output_path_for(f, codec).resolve()
        by_output.setdefault(key, []).append(f)

    collisions = {out: srcs for out, srcs in by_output.items() if len(srcs) > 1}
    if collisions:
        details = "; ".join(
            f"{out} <- {', '.join(str(s) for s in srcs)}" for out, srcs in collisions.items()
        )
        raise InvalidCompressInputError(
            f"multiple input files would produce the same output file: {details} — "
            "compress them in separate runs or rename one first"
        )
