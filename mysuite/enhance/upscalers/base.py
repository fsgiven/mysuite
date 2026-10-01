from __future__ import annotations

from abc import ABC, abstractmethod
from PIL import Image


class Upscaler(ABC):
    name: str = "base"

    @abstractmethod
    def is_available(self) -> bool:
        """Whether this backend can actually run (deps + weights present)."""

    @abstractmethod
    def upscale(self, image: Image.Image, scale: int, denoise: float = 0.3,
                sharpen: float = 0.3, face_enhance: bool = False) -> Image.Image:
        """Return an upscaled PIL Image. `denoise`/`sharpen` in [0,1]."""
