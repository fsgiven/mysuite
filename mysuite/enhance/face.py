"""Optional GFPGAN-based face restoration.

Lazily imported; if `gfpgan` isn't installed or weights are missing this
raises, and callers should catch and skip face enhancement gracefully.
"""
from __future__ import annotations

import numpy as np
from PIL import Image

from mysuite.enhance._paths import models_dir

_RESTORER = None
GFPGAN_WEIGHT_NAME = "GFPGANv1.4.pth"


def _get_restorer():
    global _RESTORER
    if _RESTORER is not None:
        return _RESTORER
    from gfpgan import GFPGANer

    weight_path = models_dir() / GFPGAN_WEIGHT_NAME
    if not weight_path.exists():
        raise FileNotFoundError(f"GFPGAN weights not found at {weight_path}")

    _RESTORER = GFPGANer(
        model_path=str(weight_path),
        upscale=1,
        arch="clean",
        channel_multiplier=2,
        bg_upsampler=None,
    )
    return _RESTORER


def enhance_faces(image: Image.Image) -> Image.Image:
    restorer = _get_restorer()
    rgb = np.array(image.convert("RGB"))
    bgr = rgb[:, :, ::-1].copy()
    _, _, restored_bgr = restorer.enhance(
        bgr, has_aligned=False, only_center_face=False, paste_back=True
    )
    restored_rgb = restored_bgr[:, :, ::-1]
    return Image.fromarray(restored_rgb)
