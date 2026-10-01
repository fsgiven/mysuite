"""Relight (experimental, classical): re-shade a photo as if a light came from another direction.

How it works - and how far it goes. There is no neural network here. The photo gets a *pseudo-depth*:
a broad blur of its own brightness, plus (if the file has transparency) a rounded "dome" from the
subject's alpha mask, so a cut-out looks round instead of flat. Surface normals come from that depth,
and a Lambert + soft-specular light (direction, height, colour, intensity, softness) re-shades the
pixels. Flat areas keep their exact brightness; slopes towards the light brighten, away darken.

It is an honest *lighting nudge* (rim of light on one side, softer shadow on the other, warm/cool
tint), good for products, logos on gradients and cut-outs; it does not know the real 3-D shape, so it
cannot add light that was never there on faces or complex scenes. A depth-model backend is planned
(see docs/ROADMAP.md).
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from mysuite.color.parse import parse_color
from mysuite.enhance.enhance import _bit_depth
from mysuite.utils.subprocess_utils import atomic_write_via

DIRECTIONS = {
    "top": 0.0, "top-right": 45.0, "right": 90.0, "bottom-right": 135.0,
    "bottom": 180.0, "bottom-left": 225.0, "left": 270.0, "top-left": 315.0,
}

# name -> options (the same names as the CLI flags)
PRESETS: dict[str, dict] = {
    "key-left": {"direction": "top-left", "height": 40, "intensity": 0.9, "ambient": 0.55},
    "key-right": {"direction": "top-right", "height": 40, "intensity": 0.9, "ambient": 0.55},
    "top": {"direction": "top", "height": 55, "intensity": 0.8, "ambient": 0.6},
    "side": {"direction": "left", "height": 25, "intensity": 1.0, "ambient": 0.4},
    "dramatic": {"direction": "left", "height": 20, "intensity": 1.3, "ambient": 0.25, "specular": 0.3},
    "golden-hour": {"direction": "right", "height": 18, "intensity": 0.9, "ambient": 0.55, "color": "#ffb36b"},
    "cool-fill": {"direction": "bottom-right", "height": 35, "intensity": 0.6, "ambient": 0.7, "color": "#9ec5ff"},
}
MAX_PIXELS = 50_000_000


class RelightError(ValueError):
    pass


@dataclass
class RelightSettings:
    direction: str | None = "top-left"    # a name from DIRECTIONS, or use angle
    angle: float | None = None            # compass degrees the light comes FROM: 0 top, 90 right, 180 bottom, 270 left
    height: float = 40.0                  # light elevation above the picture plane, degrees (90 = straight on)
    intensity: float = 0.9                # how strongly the light shapes the picture
    ambient: float = 0.55                 # base light everywhere (lower = deeper shadows)
    softness: float = 6.0                 # blur of the shape, percent of the short side
    depth: float = 1.0                    # how pronounced the pseudo-3D is
    specular: float = 0.0                 # 0 = matte; 0.3 = a soft sheen
    color: str | None = None              # tint of the light
    preset: str | None = None

    @classmethod
    def from_options(cls, preset: str | None, explicit: dict) -> "RelightSettings":
        """defaults < preset < explicitly given options (None = not given)."""
        if preset is not None and preset not in PRESETS:
            raise RelightError(f"unknown preset {preset!r} - expected one of {', '.join(PRESETS)}")
        values = {**PRESETS.get(preset or "", {}), **{k: v for k, v in explicit.items() if v is not None}}
        if explicit.get("angle") is not None:
            values.pop("direction", None)           # an explicit angle beats a named direction (even from a preset)
            values["direction"] = None
        settings = cls(preset=preset, **values)
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.angle is None and self.direction not in DIRECTIONS:
            raise RelightError(f"unknown direction {self.direction!r} - expected one of {', '.join(DIRECTIONS)} (or give --angle)")
        if not 1 <= self.height <= 90:
            raise RelightError("height must be between 1 and 90 degrees")
        if not 0 <= self.intensity <= 3:
            raise RelightError("intensity must be between 0 and 3")
        if not 0 <= self.ambient <= 2:
            raise RelightError("ambient must be between 0 and 2")
        if not 0 <= self.softness <= 50:
            raise RelightError("softness must be between 0 and 50")
        if not 0 <= self.depth <= 5:
            raise RelightError("depth must be between 0 and 5")
        if not 0 <= self.specular <= 2:
            raise RelightError("specular must be between 0 and 2")
        if self.color and parse_color(self.color) is None:
            raise RelightError(f"color: not a colour: {self.color!r}")

    def light_vector(self) -> np.ndarray:
        azimuth = math.radians(self.angle if self.angle is not None else DIRECTIONS[self.direction or "top-left"])
        elevation = math.radians(self.height)
        dx, dy = math.sin(azimuth), -math.cos(azimuth)           # image coords: x right, y down
        return np.array([dx * math.cos(elevation), dy * math.cos(elevation), math.sin(elevation)])


def _blur(a: np.ndarray, radius: float) -> np.ndarray:
    """Separable box blur x3 (~Gaussian), edge-replicated; radius in pixels."""
    r = int(round(radius))
    if r < 1:
        return a
    out = a.astype(np.float64)
    for _ in range(3):
        for axis in (0, 1):
            padded = np.pad(out, [(r, r) if i == axis else (0, 0) for i in range(out.ndim)], mode="edge")
            c = np.cumsum(padded, axis=axis, dtype=np.float64)
            zero = np.zeros_like(np.take(c, [0], axis=axis))
            c = np.concatenate([zero, c], axis=axis)
            n = out.shape[axis]
            hi = np.take(c, range(2 * r + 1, 2 * r + 1 + n), axis=axis)
            lo = np.take(c, range(0, n), axis=axis)
            out = (hi - lo) / (2 * r + 1)
    return out


def pseudo_depth(rgb: np.ndarray, alpha: np.ndarray | None, softness_px: float, depth: float) -> np.ndarray:
    luma = rgb @ np.array([0.2126, 0.7152, 0.0722])
    broad = _blur(luma, max(softness_px, 1.0))
    surface = broad * 0.35                                   # the picture's own light/dark as gentle relief
    if alpha is not None and alpha.min() < 0.99:
        short = min(alpha.shape)
        dome = _blur(alpha, short * 0.14)
        surface = surface + np.clip(dome, 0, 1) ** 0.8 * alpha
    return surface * depth


def relight_array(rgb: np.ndarray, alpha: np.ndarray | None, s: RelightSettings) -> np.ndarray:
    """rgb float 0-1 (H,W,3) -> relit float 0-1."""
    h, w, _ = rgb.shape
    short = min(h, w)
    z = pseudo_depth(rgb, alpha, short * s.softness / 100, s.depth) * short * 0.25
    dzdy, dzdx = np.gradient(z)
    normal = np.dstack([-dzdx, -dzdy, np.ones_like(z)])
    normal /= np.linalg.norm(normal, axis=2, keepdims=True)
    light = s.light_vector()
    lam = np.clip(normal @ light, 0, 1)
    flat = light[2]
    gain = (s.ambient + s.intensity * lam) / (s.ambient + s.intensity * flat)       # flat surfaces: exactly 1
    out = rgb * gain[..., None]
    if s.color:
        c = parse_color(s.color)
        tint = np.array([c.r, c.g, c.b], dtype=np.float64) / 255.0
        t = np.clip(gain - 1.0, 0, 1)[..., None]
        out = out * (1 + (tint - 1.0) * t * 1.2)
    if s.specular > 0:
        half = light + np.array([0, 0, 1.0])
        half /= np.linalg.norm(half)
        spec = np.clip(normal @ half, 0, 1) ** 24 * s.specular * s.intensity
        tint = np.array([1.0, 1.0, 1.0]) if not s.color else np.array([parse_color(s.color).r, parse_color(s.color).g, parse_color(s.color).b]) / 255.0
        out = out + spec[..., None] * tint * 0.6
    return np.clip(out, 0, 1)


@dataclass
class RelightOutcome:
    input_path: Path
    output_path: Path
    status: str
    size: tuple[int, int] = (0, 0)
    notes: list[str] = field(default_factory=list)


def output_path_for(input_path: Path) -> Path:
    return input_path.with_name(f"{input_path.stem}_relit.png")


def relight_file(input_path: Path, settings: RelightSettings, *, overwrite: bool = False) -> RelightOutcome:
    input_path = Path(os.path.abspath(input_path))
    out = output_path_for(input_path)
    if out.exists() and not overwrite:
        return RelightOutcome(input_path, out, "skipped_existing")
    notes: list[str] = []
    try:
        with Image.open(input_path) as opened:
            if opened.width * opened.height > MAX_PIXELS:
                raise RelightError(f"image is {opened.width * opened.height / 1e6:.0f} MP; the limit is {MAX_PIXELS // 1_000_000} MP")
            if getattr(opened, "n_frames", 1) > 1:
                notes.append("animated source: only the first frame was relit")
            if _bit_depth(opened, input_path) > 8:
                notes.append("source is more than 8 bits per channel; the output is 8-bit")
            opened.seek(0)
            icc = opened.info.get("icc_profile")
            image = ImageOps.exif_transpose(opened).convert("RGBA")
    except RelightError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise RelightError(f"can't read {input_path.name}: {exc}") from exc
    arr = np.asarray(image, dtype=np.float64) / 255.0
    rgb, alpha = arr[..., :3], arr[..., 3]
    has_alpha = alpha.min() < 0.99
    lit = relight_array(rgb, alpha if has_alpha else None, settings)
    result = np.dstack([lit, alpha]) if has_alpha else lit
    img = Image.fromarray((result * 255 + 0.5).astype(np.uint8), "RGBA" if has_alpha else "RGB")

    def write(tmp: Path) -> None:
        img.save(tmp, format="PNG", **({"icc_profile": icc} if icc else {}))

    atomic_write_via(out, write, preserve_extension=True)
    return RelightOutcome(input_path, out, "written", img.size, notes)
