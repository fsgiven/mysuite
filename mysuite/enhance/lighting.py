"""Lighting correction: gamma."""
from __future__ import annotations

import numpy as np
from PIL import Image


def apply_gamma(image: Image.Image, gamma: float) -> Image.Image:
    if abs(gamma - 1.0) < 1e-3:
        return image
    arr = np.asarray(image.convert("RGB")).astype(np.float32) / 255.0
    corrected = np.power(np.clip(arr, 0, 1), gamma)
    return Image.fromarray((corrected * 255).round().astype(np.uint8))
