"""Backend selection: picks the best available upscaler.

Order of preference:
1. Real-ESRGAN (if `realesrgan`/`torch` are importable and weights exist)
2. Classical Pillow/NumPy Lanczos + unsharp mask (always available)
"""
from __future__ import annotations

from typing import Optional

from .base import Upscaler
from .classical import ClassicalUpscaler

_REALESRGAN_UPSCALER: Optional[Upscaler] = None
_REALESRGAN_CHECKED = False


def _try_realesrgan() -> Optional[Upscaler]:
    global _REALESRGAN_UPSCALER, _REALESRGAN_CHECKED
    if _REALESRGAN_CHECKED:
        return _REALESRGAN_UPSCALER
    _REALESRGAN_CHECKED = True
    try:
        from .realesrgan_backend import RealESRGANUpscaler
        _REALESRGAN_UPSCALER = RealESRGANUpscaler()
        if not _REALESRGAN_UPSCALER.is_available():
            _REALESRGAN_UPSCALER = None
    except Exception:
        _REALESRGAN_UPSCALER = None
    return _REALESRGAN_UPSCALER


def get_upscaler(prefer: str = "auto") -> Upscaler:
    """Return an Upscaler instance.

    prefer: "auto" | "realesrgan" | "classical"
    """
    if prefer == "classical":
        return ClassicalUpscaler()
    if prefer in ("auto", "realesrgan"):
        ai = _try_realesrgan()
        if ai is not None:
            return ai
        if prefer == "realesrgan":
            raise RuntimeError(
                "Real-ESRGAN backend requested but not available. "
                "Install with `pip install torch realesrgan basicsr` and "
                "download model weights (see README)."
            )
    return ClassicalUpscaler()


def backend_status() -> dict:
    ai = _try_realesrgan()
    return {
        "realesrgan_available": ai is not None,
        "classical_available": True,
    }
