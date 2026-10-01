from __future__ import annotations

import io
import time
from dataclasses import dataclass, field, fields
from pathlib import Path

from PIL import Image, ImageCms, ImageOps, UnidentifiedImageError

from mysuite.enhance import color, lighting, scratch
from mysuite.enhance._parsing import OUTPUT_EXTENSIONS, output_path_for
from mysuite.enhance.presets import ENHANCE_DEFAULTS
from mysuite.enhance.upscalers import get_upscaler
from mysuite.utils.subprocess_utils import atomic_write_via

MAX_OUTPUT_MEGAPIXELS = 250.0


class EnhanceError(RuntimeError):
    pass


@dataclass
class EnhanceSettings:
    scale: int = ENHANCE_DEFAULTS["scale"]
    denoise: float = ENHANCE_DEFAULTS["denoise"]
    sharpen: float = ENHANCE_DEFAULTS["sharpen"]
    face_enhance: bool = ENHANCE_DEFAULTS["face_enhance"]
    auto_white_balance: bool = ENHANCE_DEFAULTS["auto_white_balance"]
    saturation: float = ENHANCE_DEFAULTS["saturation"]
    contrast: float = ENHANCE_DEFAULTS["contrast"]
    gamma: float = ENHANCE_DEFAULTS["gamma"]
    restore_scratches: bool = ENHANCE_DEFAULTS["restore_scratches"]
    output_format: str = ENHANCE_DEFAULTS["output_format"]
    output_quality: int = ENHANCE_DEFAULTS["output_quality"]

    @classmethod
    def from_dict(cls, data: dict) -> "EnhanceSettings":
        """Builds validated settings from a (preset + overrides) dict. Unknown
        keys are an error — a typo in a TOML preset shouldn't be silently ignored."""
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise EnhanceError(f"unknown enhance setting(s): {', '.join(sorted(unknown))}")
        settings = cls(**data)
        settings.output_format = {"jpeg": "jpg"}.get(settings.output_format, settings.output_format)
        settings.validate()
        return settings

    def validate(self) -> None:
        def check(ok: bool, message: str) -> None:
            if not ok:
                raise EnhanceError(message)

        check(isinstance(self.scale, int) and 1 <= self.scale <= 8, f"scale must be an integer 1-8: {self.scale!r}")
        check(0.0 <= self.denoise <= 1.0, f"denoise must be 0-1: {self.denoise!r}")
        check(0.0 <= self.sharpen <= 1.0, f"sharpen must be 0-1: {self.sharpen!r}")
        check(0.0 <= self.saturation <= 3.0, f"saturation must be 0-3: {self.saturation!r}")
        check(0.0 <= self.contrast <= 3.0, f"contrast must be 0-3: {self.contrast!r}")
        check(0.2 <= self.gamma <= 3.0, f"gamma must be 0.2-3: {self.gamma!r}")
        check(self.output_format in OUTPUT_EXTENSIONS, f"output format must be one of {', '.join(OUTPUT_EXTENSIONS)}: {self.output_format!r}")
        check(1 <= self.output_quality <= 100, f"quality must be 1-100: {self.output_quality!r}")


@dataclass
class EnhanceOutcome:
    input_path: Path
    output_path: Path
    status: str  # "written" | "skipped_existing"
    backend_used: str = ""
    input_size: tuple[int, int] = (0, 0)
    output_size: tuple[int, int] = (0, 0)
    duration_seconds: float = 0.0
    notes: list[str] = field(default_factory=list)


def _to_srgb(image: Image.Image, notes: list[str]) -> Image.Image:
    """Converts to RGB in the sRGB space. Saving with Pillow drops the embedded
    profile, so a Display-P3/AdobeRGB source would otherwise come out visibly
    desaturated; converting first keeps colors right, and the output carries no
    profile (nothing to identify a calibrated screen or device)."""
    profile = image.info.get("icc_profile")
    if profile:
        try:
            source = ImageCms.ImageCmsProfile(io.BytesIO(profile))
            srgb = ImageCms.createProfile("sRGB")
            converted = ImageCms.profileToProfile(image, source, srgb, outputMode="RGB")
            converted.info.pop("icc_profile", None)  # lcms attaches its own; untagged RGB already means sRGB
            return converted
        except (ImageCms.PyCMSError, OSError, ValueError):
            notes.append("embedded color profile couldn't be applied; colors may shift")
    return image.convert("RGB")


def _load(input_path: Path, notes: list[str]) -> tuple[Image.Image, Image.Image | None]:
    """Returns (rgb, alpha-or-None). Orientation is baked into the pixels —
    the output has no EXIF, so a rotation flag would be lost and phone photos
    would come out sideways."""
    try:
        image = Image.open(input_path)
        image.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise EnhanceError(f"can't read {input_path.name}: {exc}") from exc

    image = ImageOps.exif_transpose(image)
    icc = image.info.get("icc_profile")
    alpha = None
    if image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info:
        rgba = image.convert("RGBA")
        alpha = rgba.getchannel("A")
        image = rgba.convert("RGB")
        if icc:
            image.info["icc_profile"] = icc
    return _to_srgb(image, notes), alpha


def _save(image: Image.Image, alpha: Image.Image | None, output_path: Path, settings: EnhanceSettings) -> None:
    fmt = settings.output_format
    if alpha is not None and fmt == "jpg":
        flat = Image.new("RGB", image.size, "white")
        flat.paste(image, mask=alpha)
        image = flat
    elif alpha is not None:
        image = image.convert("RGBA")
        image.putalpha(alpha)

    def write(tmp: Path) -> None:
        if fmt == "jpg":
            image.save(tmp, format="JPEG", quality=settings.output_quality, optimize=True)
        elif fmt == "webp":
            image.save(tmp, format="WEBP", quality=settings.output_quality)
        else:
            image.save(tmp, format="PNG", optimize=True)

    atomic_write_via(output_path, write)


def enhance_file(
    input_path: Path,
    settings: EnhanceSettings,
    *,
    backend: str = "auto",
    overwrite: bool = False,
) -> EnhanceOutcome:
    """Restores/upscales one photo, writing <name>_enhanced.<ext> beside it.
    Never modifies the source. Stages, in order: scratch reduction (native
    resolution, where thin lines are crispest), upscale + denoise + sharpen,
    color, gamma. The result has no EXIF/XMP/ICC — see _load/_to_srgb."""
    output_path = output_path_for(input_path, settings.output_format)
    if not overwrite and output_path.exists():
        return EnhanceOutcome(input_path, output_path, "skipped_existing")

    started = time.time()
    notes: list[str] = []
    rgb, alpha = _load(input_path, notes)
    input_size = rgb.size

    out_mp = rgb.width * rgb.height * settings.scale**2 / 1e6
    if out_mp > MAX_OUTPUT_MEGAPIXELS:
        raise EnhanceError(
            f"{input_path.name}: {settings.scale}x would be {out_mp:.0f} MP "
            f"(limit {MAX_OUTPUT_MEGAPIXELS:.0f} MP) — use a smaller scale"
        )

    if settings.restore_scratches:
        rgb = scratch.reduce_scratches(rgb)

    try:
        upscaler = get_upscaler(prefer=backend)
    except RuntimeError as exc:
        raise EnhanceError(str(exc)) from exc

    rgb = upscaler.upscale(
        rgb, scale=settings.scale, denoise=settings.denoise,
        sharpen=settings.sharpen, face_enhance=settings.face_enhance,
    )
    if settings.face_enhance:
        if upscaler.name == "classical":
            notes.append("face enhancement skipped — it needs the optional AI backend")
        elif getattr(upscaler, "face_error", None):
            notes.append(f"face enhancement skipped — {upscaler.face_error}")

    rgb = color.apply_color_stage(
        rgb, auto_wb=settings.auto_white_balance,
        saturation=settings.saturation, contrast=settings.contrast,
    )
    rgb = lighting.apply_gamma(rgb, settings.gamma)

    if alpha is not None:
        alpha = alpha.resize(rgb.size, Image.LANCZOS)

    _save(rgb, alpha, output_path, settings)
    return EnhanceOutcome(
        input_path, output_path, "written", backend_used=upscaler.name,
        input_size=input_size, output_size=rgb.size,
        duration_seconds=time.time() - started, notes=notes,
    )
