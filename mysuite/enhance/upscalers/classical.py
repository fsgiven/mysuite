"""Classical, dependency-light upscaler.

Always available (Pillow + NumPy only). Uses high-quality Lanczos
resampling combined with an unsharp mask and a light median-filter
denoise pass. This is the guaranteed fallback backend so the app never
requires a GPU or model downloads to be useful out of the box.
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter

from .base import Upscaler


class ClassicalUpscaler(Upscaler):
    name = "classical"

    def is_available(self) -> bool:
        return True

    def upscale(self, image: Image.Image, scale: int, denoise: float = 0.3,
                sharpen: float = 0.3, face_enhance: bool = False) -> Image.Image:
        image = image.convert("RGB")
        w, h = image.size
        target = (max(1, w * scale), max(1, h * scale))

        if denoise > 0:
            image = self._denoise(image, strength=denoise)

        upscaled = image.resize(target, resample=Image.LANCZOS)
        if sharpen > 0:
            # sharpen drives the base amount directly (0 = skip entirely, so a
            # preset with sharpen=0 really does produce zero unsharp masking);
            # the upscale factor is a secondary modifier, since a bigger
            # resize softens detail more and benefits from more correction.
            amount = sharpen * (0.5 + 0.5 * min(1.0, scale / 4))
            upscaled = self._unsharp(upscaled, amount=amount)

        # face_enhance is a no-op on the classical backend; the AI backend
        # (GFPGAN) is responsible for real face restoration. We still allow
        # the flag so presets are backend-agnostic.
        return upscaled

    @staticmethod
    def _denoise(image: Image.Image, strength: float) -> Image.Image:
        strength = max(0.0, min(1.0, strength))
        if strength < 0.05:
            return image
        radius = 1 if strength < 0.5 else 2
        denoised = image.filter(ImageFilter.MedianFilter(size=2 * radius + 1))
        arr_orig = np.asarray(image).astype(np.float32)
        arr_denoised = np.asarray(denoised).astype(np.float32)
        blended = arr_orig * (1 - strength) + arr_denoised * strength
        return Image.fromarray(np.clip(blended, 0, 255).astype(np.uint8))

    @staticmethod
    def _unsharp(image: Image.Image, amount: float) -> Image.Image:
        amount = max(0.0, min(1.0, amount))
        percent = int(50 + amount * 150)  # 50-200
        return image.filter(ImageFilter.UnsharpMask(radius=2, percent=percent, threshold=2))
