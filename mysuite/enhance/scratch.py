"""Classical scratch reduction (Pillow + NumPy only, no model).

Targets what old photos and scans actually have: thin, straight, high-contrast
lines (film scratches, fold creases, scanner hairs). Pipeline:

1. Candidate pixels = where luminance differs from a 5x5 median of itself by
   more than a threshold. A median ignores features thinner than half its
   window, so a 1-2px scratch stands out while smooth gradients don't.
2. Keep only candidates lying on a straight run of >= RUN_LENGTH candidates
   (horizontal, vertical or either diagonal). That is what separates a scratch
   from legitimate fine detail such as eyelashes, text strokes or grain.
3. Dilate the mask by one pixel to cover the scratch's anti-aliased edge.
4. Fill masked pixels with the average of nearby UNmasked pixels (normalized
   box average), so the fill comes from clean surroundings, not the scratch.

Only masked pixels are changed; every other pixel is returned bit-for-bit. This
is not generative inpainting: wide damage, torn areas, faint and short scratches
are out of scope and left alone. Isolated dust specks are the denoise stage's
job. Limitation: any thin, straight, high-contrast line looks like a scratch,
so power lines or rigging in a photo can be partly filled in; that's why it is
off by default and only the old-photo preset enables it.
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter

# Tuned on real photos, not just synthetic scratches. At the original
# threshold=14/run=9 the detector flagged door frames and hair strands as
# "scratches" (~0.3% of pixels on ordinary camera photos). Sweeping threshold x
# run length on 7 undamaged camera JPEGs plus synthetic scratches laid over
# real texture, 28/25 flags 0.000% of pixels on the real photos while still
# catching ~93% of scratches at +60 luminance or stronger. The price is that
# faint scratches (~+30) are mostly missed (~45%) — the safe direction for a
# tool that edits pixels. Very short scratches (< RUN_LENGTH px) are skipped.
THRESHOLD = 28   # min luminance outlier (0-255) to be a scratch candidate
RUN_LENGTH = 25  # min straight run of candidates to count as a line
FILL_RADIUS = 6  # neighborhood (px) the fill is averaged over


def _count_along(mask: np.ndarray, dx: int) -> np.ndarray:
    """For each pixel, the length of the run of True ending at it when walking
    down the rows, shifting dx columns per row (dx=0 vertical, +-1 diagonal)."""
    h, w = mask.shape
    run = np.zeros((h, w), dtype=np.int32)
    run[0] = mask[0]
    for y in range(1, h):
        prev = run[y - 1]
        if dx == 0:
            before = prev
        else:
            before = np.zeros(w, dtype=np.int32)
            if dx == 1:
                before[1:] = prev[:-1]
            else:
                before[:-1] = prev[1:]
        run[y] = (before + 1) * mask[y]
    return run


def _run_lengths(mask: np.ndarray, dx: int) -> np.ndarray:
    """Length of the straight run (vertical if dx=0, diagonal if +-1) passing
    through each True pixel; 0 elsewhere."""
    forward = _count_along(mask, dx)
    backward = _count_along(mask[::-1], -dx)[::-1]
    return np.where(mask, forward + backward - 1, 0)


def _line_mask(candidates: np.ndarray, run_length: int) -> np.ndarray:
    vertical = _run_lengths(candidates, 0) >= run_length
    horizontal = _run_lengths(candidates.T, 0).T >= run_length
    diag_down = _run_lengths(candidates, 1) >= run_length
    diag_up = _run_lengths(candidates, -1) >= run_length
    return vertical | horizontal | diag_down | diag_up


def _dilate(mask: np.ndarray) -> np.ndarray:
    h, w = mask.shape
    padded = np.pad(mask, 1)
    out = np.zeros_like(mask)
    for dy in range(3):
        for dx in range(3):
            out |= padded[dy:dy + h, dx:dx + w]
    return out


def _box_sum(a: np.ndarray, r: int) -> np.ndarray:
    """Sum over a (2r+1)x(2r+1) window around each pixel (zero outside)."""
    h, w = a.shape[:2]
    padded = np.pad(a, [(r + 1, r), (r + 1, r)] + [(0, 0)] * (a.ndim - 2))
    cumulative = padded.cumsum(axis=0).cumsum(axis=1)
    size = 2 * r + 1
    return (
        cumulative[size:size + h, size:size + w]
        - cumulative[:h, size:size + w]
        - cumulative[size:size + h, :w]
        + cumulative[:h, :w]
    )


def find_scratch_mask(image: Image.Image) -> np.ndarray:
    """Boolean H x W mask of pixels judged to be part of a scratch."""
    luma = image.convert("L")
    median = np.asarray(luma.filter(ImageFilter.MedianFilter(5)), dtype=np.int16)
    outlier = np.abs(np.asarray(luma, dtype=np.int16) - median) > THRESHOLD
    return _dilate(_line_mask(outlier, RUN_LENGTH))


def reduce_scratches(image: Image.Image) -> Image.Image:
    """Returns image with thin straight scratches filled from their surroundings."""
    rgb = image.convert("RGB")
    mask = find_scratch_mask(rgb)
    if not mask.any():
        return rgb

    pixels = np.asarray(rgb, dtype=np.float64)
    valid = (~mask).astype(np.float64)
    weighted = _box_sum(pixels * valid[..., None], FILL_RADIUS)
    weight = _box_sum(valid, FILL_RADIUS)[..., None]
    fillable = mask[..., None] & (weight > 0)
    fill = np.divide(weighted, weight, out=pixels.copy(), where=weight > 0)

    result = np.where(fillable, fill, pixels)
    return Image.fromarray(np.clip(result.round(), 0, 255).astype(np.uint8))
