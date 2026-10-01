"""Optional Real-ESRGAN backend.

This module is only imported lazily by upscalers/__init__.py. It requires
`torch`, `realesrgan`, and `basicsr` to be installed, plus a downloaded
model weight file in the models cache directory. If any of that is
missing, `is_available()` returns False and the app falls back to the
classical backend automatically.
"""
from __future__ import annotations

import numpy as np
from PIL import Image

from .base import Upscaler
from mysuite.enhance._paths import models_dir

DEFAULT_WEIGHT_NAME = "RealESRGAN_x4plus.pth"


class RealESRGANUpscaler(Upscaler):
    name = "realesrgan"

    def __init__(self) -> None:
        self._model = None
        self._device = None
        self._checked = False
        self._available = False
        self.face_error: str | None = None

    def _lazy_init(self) -> None:
        if self._checked:
            return
        self._checked = True
        try:
            import torch
            from basicsr.archs.rrdbnet_arch import RRDBNet
            from realesrgan import RealESRGANer

            weight_path = models_dir() / DEFAULT_WEIGHT_NAME
            if not weight_path.exists():
                self._available = False
                return

            if torch.cuda.is_available():
                device = "cuda"
            elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
                device = "mps"
            else:
                device = "cpu"
            self._device = device

            model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64,
                             num_block=23, num_grow_ch=32, scale=4)
            self._model = RealESRGANer(
                scale=4,
                model_path=str(weight_path),
                model=model,
                tile=256,
                tile_pad=10,
                pre_pad=0,
                half=(device == "cuda"),
                device=device,
            )
            self._available = True
        except Exception:
            self._model = None
            self._available = False

    def is_available(self) -> bool:
        self._lazy_init()
        return self._available

    def upscale(self, image: Image.Image, scale: int, denoise: float = 0.3,
                sharpen: float = 0.3, face_enhance: bool = False) -> Image.Image:
        # sharpen is intentionally unused here — Real-ESRGAN's own learned
        # detail reconstruction already sharpens far more accurately than a
        # post-hoc unsharp mask could, so re-applying one on top would just
        # reintroduce classical-style haloing. Accepted for interface parity
        # with ClassicalUpscaler (presets stay backend-agnostic).
        self._lazy_init()
        if not self._available or self._model is None:
            raise RuntimeError("Real-ESRGAN backend is not available")

        rgb = np.array(image.convert("RGB"))
        bgr = rgb[:, :, ::-1].copy()
        output, _ = self._model.enhance(bgr, outscale=scale)
        rgb_out = output[:, :, ::-1]
        result = Image.fromarray(rgb_out)

        if face_enhance:
            try:
                from mysuite.enhance.face import enhance_faces
                result = enhance_faces(result)
            except Exception as exc:  # optional dependency / weights missing
                self.face_error = f"{type(exc).__name__}: {exc}"

        return result
