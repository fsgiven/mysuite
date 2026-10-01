from __future__ import annotations

import numpy as np
from PIL import Image

from mysuite.enhance.scratch import find_scratch_mask, reduce_scratches


def _photo(seed: int = 0, size=(300, 200)) -> np.ndarray:
    rng = np.random.default_rng(seed)
    h, w = size[1], size[0]
    yy, xx = np.mgrid[0:h, 0:w]
    base = np.stack([100 + 0.2 * xx + 0.1 * yy, 120 + 0.1 * xx + 0.15 * yy, 160 - 0.1 * xx + 0.1 * yy], -1)
    return np.clip(base + rng.normal(0, 2.5, base.shape), 0, 255)


def _as_image(arr: np.ndarray) -> Image.Image:
    return Image.fromarray(np.clip(arr.round(), 0, 255).astype(np.uint8))


def test_vertical_scratch_is_filled_from_its_surroundings():
    clean = _photo()
    damaged = clean.copy()
    damaged[20:180, 120] = 250
    damaged[20:180, 121] = 205
    out = np.asarray(reduce_scratches(_as_image(damaged))).astype(float)
    cols = (slice(20, 180), slice(120, 122))
    assert np.abs(damaged[cols] - clean[cols]).mean() > 60
    assert np.abs(out[cols] - clean[cols]).mean() < 6


def test_diagonal_dark_scratch_is_filled():
    clean = _photo()
    damaged = clean.copy()
    for i in range(150):
        damaged[20 + i, 60 + i] = 10
    out = np.asarray(reduce_scratches(_as_image(damaged))).astype(float)
    idx = (np.arange(150) + 20, np.arange(150) + 60)
    assert np.abs(damaged[idx] - clean[idx]).mean() > 60
    assert np.abs(out[idx] - clean[idx]).mean() < 8


def test_only_masked_pixels_change():
    damaged = _photo()
    damaged[20:180, 120] = 250
    img = _as_image(damaged)
    mask = find_scratch_mask(img)
    out = np.asarray(reduce_scratches(img))
    before = np.asarray(img)
    changed = (out != before).any(axis=-1)
    assert mask.any()
    assert not (changed & ~mask).any()


def test_short_strokes_like_text_or_eyelashes_are_left_alone():
    damaged = _photo()
    damaged[100:105, 300 - 10] = 250  # a 5px stroke, well under the line-run threshold
    img = _as_image(damaged)
    assert not find_scratch_mask(img).any()
    assert np.array_equal(np.asarray(reduce_scratches(img)), np.asarray(img))


def test_grain_alone_and_clean_gradients_are_not_touched():
    img = _as_image(_photo(seed=3))
    assert not find_scratch_mask(img).any()
    assert np.array_equal(np.asarray(reduce_scratches(img)), np.asarray(img))


def test_a_real_edge_is_not_mistaken_for_a_scratch():
    arr = _photo()
    arr[:, 150:] += 60  # a long vertical step edge, like a doorframe
    img = _as_image(np.clip(arr, 0, 255))
    out = np.asarray(reduce_scratches(img)).astype(float)
    assert np.abs(out - np.asarray(img)).mean() < 0.05


def test_faint_scratches_are_deliberately_left_alone():
    # Precision over recall: a +20 line is within normal texture/edge contrast, and
    # a tool that rewrites pixels must not guess. (Documented in scratch.py.)
    damaged = _photo()
    damaged[10:180, 100] += 20
    img = _as_image(damaged)
    assert not find_scratch_mask(img).any()


def test_strong_but_only_somewhat_short_scratch_still_removed():
    clean = _photo()
    damaged = clean.copy()
    damaged[40:80, 150] = 245  # 40px, above the 25px minimum
    out = np.asarray(reduce_scratches(_as_image(damaged))).astype(float)
    assert np.abs(out[40:80, 150] - clean[40:80, 150]).mean() < 8
