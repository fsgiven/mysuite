"""Color correction: auto white balance, saturation, contrast.

Pure NumPy/Pillow implementation, no external model needed.
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageEnhance


def auto_white_balance(image: Image.Image, strength: float = 1.0) -> Image.Image:
    """Simple gray-world white balance."""
    arr = np.asarray(image.convert("RGB")).astype(np.float32)
    means = arr.reshape(-1, 3).mean(axis=0)
    overall_mean = means.mean()
    if overall_mean < 1e-6:
        return image
    gains = overall_mean / np.clip(means, 1e-6, None)
    gains = 1.0 + (gains - 1.0) * strength
    balanced = arr * gains[np.newaxis, np.newaxis, :]
    return Image.fromarray(np.clip(balanced, 0, 255).astype(np.uint8))


def adjust_saturation(image: Image.Image, factor: float) -> Image.Image:
    if abs(factor - 1.0) < 1e-3:
        return image
    return ImageEnhance.Color(image).enhance(factor)


def adjust_contrast(image: Image.Image, factor: float) -> Image.Image:
    if abs(factor - 1.0) < 1e-3:
        return image
    return ImageEnhance.Contrast(image).enhance(factor)


def apply_color_stage(image: Image.Image, auto_wb: bool, saturation: float,
                       contrast: float) -> Image.Image:
    if auto_wb:
        image = auto_white_balance(image)
    image = adjust_saturation(image, saturation)
    image = adjust_contrast(image, contrast)
    return image
