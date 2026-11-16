"""One RGB -> CMYK engine for every format (PDF, EPS, TIFF).

Before this, PDF/EPS were converted by Ghostscript and TIFF by ImageMagick, so one brand
red came out as different inks per file, and neutral greys became noisy "rich" mixes.
Now every flat colour goes through the same ICC transform (LittleCMS via Pillow) and the
same rules:

* ``exact``      keep the profile's numbers.
* ``clean[:N]``  snap each channel to the nearest multiple of N (default 5: 73/92 -> 75/90),
                 then <=3 -> 0 and >=97 -> 100. The colour shift (CIEDE2000) is reported.
* neutrals (R=G=B) are always K-only, with the K that best matches that grey in the profile
  (#222 -> 0/0/0/98, #000 -> 0/0/0/100): no rich-black mixes.
* brand values (``cmyk_map``, e.g. ``#DD0000=0/100/100/0``) win over all of the above: a brand book's
  print values are used as they are, never computed, rounded or made K-only.
"""
from __future__ import annotations

import glob
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

from PIL import Image, ImageCms

from mysuite.color.lab import delta_e_rgb
from mysuite.color.parse import parse_color

MODES = ("exact", "clean")
TOTAL_INK_LIMIT = 300.0
BRAND_TOLERANCE = 2.0  # CIEDE2000: a just-noticeable difference; nearer colours take the brand value

RGB = tuple[int, int, int]
CMYK = tuple[float, float, float, float]


class CmykError(ValueError):
    pass


_NUMBER = r"\d+(?:\.\d+)?"
_PAIR_RE = re.compile(rf"\s*([^\s=,]+)\s*=\s*({_NUMBER}(?:\s*[/,\s]\s*{_NUMBER})*)\s*,?")


def _parse_inks(colour: str, text: str) -> CMYK:
    parts = re.split(r"\s*[/,\s]\s*", text.strip())
    try:
        values = tuple(float(p) for p in parts)
    except ValueError as exc:
        raise CmykError(f"--cmyk-map {colour}: CMYK must be four numbers like 0/100/100/0, got {text!r}") from exc
    if len(values) != 4:
        raise CmykError(f"--cmyk-map {colour}: CMYK needs four numbers (C/M/Y/K), got {len(values)} in {text!r}")
    if any(not 0 <= v <= 100 for v in values):
        raise CmykError(f"--cmyk-map {colour}: each CMYK value must be 0-100 percent, got {text!r}")
    return tuple(int(v) if v.is_integer() else v for v in values)  # type: ignore[return-value]


def _parse_colour(text: str) -> RGB:
    colour = parse_color(text)
    if colour is None:
        raise CmykError(f"--cmyk-map: {text!r} is not a colour (use hex like #DD0000 or a CSS name)")
    return (colour.r, colour.g, colour.b)


def parse_cmyk_map(value: Iterable[str] | Mapping[str, str] | None) -> dict[RGB, CMYK]:
    """Brand CMYK values: '#DD0000=0/100/100/0' (also 0,100,100,0 or 0 100 100 0), several pairs per item,
    or a {colour: "c/m/y/k"} table from mysuite.toml. -> {(r, g, b): (c, m, y, k)} in percent."""
    if not value:
        return {}
    if isinstance(value, Mapping):
        return {_parse_colour(str(k)): _parse_inks(str(k), str(v)) for k, v in value.items()}
    out: dict[RGB, CMYK] = {}
    for item in value:
        text, pos = str(item), 0
        while pos < len(text.rstrip()):
            m = _PAIR_RE.match(text, pos)
            if not m or m.end() == pos:
                raise CmykError(
                    f"invalid --cmyk-map {text[pos:].strip()!r} - expected COLOUR=C/M/Y/K, e.g. '#DD0000=0/100/100/0'"
                )
            colour, inks = m.group(1), m.group(2)
            out[_parse_colour(colour)] = _parse_inks(colour, inks)
            pos = m.end()
    return out


@dataclass(frozen=True)
class CmykSettings:
    mode: str = "exact"
    step: int = 5
    profile: str | None = None  # path to an ICC file; None = auto-detect
    cmyk_map: tuple[tuple[RGB, CMYK], ...] = ()  # brand values, used as they are

    @classmethod
    def parse(cls, mode: str | None, profile: str | None = None, cmyk_map: Mapping[RGB, CMYK] | None = None) -> "CmykSettings":
        """'exact' | 'clean' | 'clean:10'; cmyk_map from parse_cmyk_map()."""
        text = (mode or "exact").strip().lower()
        name, _, arg = text.partition(":")
        if name not in MODES:
            raise CmykError(f"unknown CMYK mode {mode!r} - expected exact, clean or clean:<step>")
        step = 5
        if arg:
            try:
                step = int(arg)
            except ValueError as exc:
                raise CmykError(f"CMYK clean step must be a whole number, got {arg!r}") from exc
            if not 1 <= step <= 25:
                raise CmykError("CMYK clean step must be between 1 and 25")
        if name == "exact" and arg:
            raise CmykError("CMYK mode 'exact' takes no step")
        return cls(name, step, profile, tuple(sorted((cmyk_map or {}).items())))


def find_default_profile(gs_binary: str = "gs") -> Path | None:
    """The CMYK profile Ghostscript itself uses (SWOP-like, redistributable), else macOS's generic one."""
    gs = shutil.which(gs_binary)
    if gs:
        prefix = Path(os.path.realpath(gs)).parent.parent
        for pattern in ("share/ghostscript/iccprofiles", "share/ghostscript/*/iccprofiles", "share/ghostscript/*/Resource/ColorSpace"):
            hits = sorted(glob.glob(str(prefix / pattern / "default_cmyk.icc")))
            if hits:
                return Path(hits[-1])
    mac = Path("/System/Library/ColorSync/Profiles/Generic CMYK Profile.icc")
    return mac if mac.exists() else None


@dataclass
class Conversion:
    rgb: tuple[int, int, int]
    cmyk: tuple[float, float, float, float]  # percent
    delta_e: float
    neutral: bool
    fixed: bool = False  # True = a brand value from cmyk_map, not computed

    def label(self) -> str:
        c, m, y, k = (f"{v:g}" for v in self.cmyk)
        note = "brand value" if self.fixed else f"dE {self.delta_e:.1f}"
        return f"#{self.rgb[0]:02x}{self.rgb[1]:02x}{self.rgb[2]:02x} -> {c}/{m}/{y}/{k}  ({note})"


@dataclass
class CmykEngine:
    settings: CmykSettings = field(default_factory=CmykSettings)
    gs_binary: str = "gs"

    def __post_init__(self) -> None:
        path = Path(self.settings.profile).expanduser() if self.settings.profile else find_default_profile(self.gs_binary)
        if path is None or not path.exists():
            raise CmykError(
                "no CMYK ICC profile found - pass --cmyk-profile PATH "
                "(Ghostscript normally ships default_cmyk.icc; macOS has Generic CMYK Profile.icc)"
            )
        try:
            self.profile_path = path
            self._cmyk = ImageCms.getOpenProfile(str(path))
            if self._cmyk.profile.xcolor_space.strip() != "CMYK":
                raise CmykError(f"{path.name} is not a CMYK profile")
        except ImageCms.PyCMSError as exc:
            raise CmykError(f"can't read ICC profile {path}: {exc}") from exc
        srgb = ImageCms.createProfile("sRGB")
        intent = ImageCms.Intent.RELATIVE_COLORIMETRIC
        flags = ImageCms.Flags.BLACKPOINTCOMPENSATION
        self._to_cmyk = ImageCms.buildTransform(srgb, self._cmyk, "RGB", "CMYK", renderingIntent=intent, flags=flags)
        self._to_rgb = ImageCms.buildTransform(self._cmyk, srgb, "CMYK", "RGB", renderingIntent=intent, flags=flags)
        self._cache: dict[tuple[int, int, int], Conversion] = {}
        self._fixed: dict[RGB, CMYK] = dict(self.settings.cmyk_map)
        self.description = ImageCms.getProfileDescription(self._cmyk).strip()

    @property
    def icc_bytes(self) -> bytes:
        return self.profile_path.read_bytes()

    # ------------------------------------------------------------------ colours
    def _snap(self, value: float) -> float:
        step = self.settings.step
        snapped = round(value / step) * step
        snapped = min(100.0, max(0.0, float(snapped)))
        return 0.0 if snapped <= 3 else 100.0 if snapped >= 97 else snapped

    def convert(self, r: int, g: int, b: int) -> Conversion:
        key = (r, g, b)
        hit = self._cache.get(key)
        if hit:
            return hit
        neutral = max(key) - min(key) <= 1
        brand = key if key in self._fixed else self._nearest_brand(key)
        if brand is not None:
            fixed = self._fixed[brand]
            result = Conversion(key, fixed, self._delta_e(key, fixed), neutral, fixed=True)
            self._cache[key] = result
            return result
        if neutral:
            cmyk = (0.0, 0.0, 0.0, self._neutral_k(round((r + g + b) / 3)))
        else:
            px = ImageCms.applyTransform(Image.new("RGB", (1, 1), key), self._to_cmyk).getpixel((0, 0))
            cmyk = tuple(round(v / 255 * 100, 1) for v in px)
        if self.settings.mode == "clean":
            cmyk = tuple(self._snap(v) for v in cmyk)
        result = Conversion(key, cmyk, self._delta_e(key, cmyk), neutral)  # type: ignore[arg-type]
        self._cache[key] = result
        return result

    def _nearest_brand(self, rgb: RGB) -> RGB | None:
        """A brand colour this one is indistinguishable from (anti-aliased edge pixels like #dd0101 next to
        #dd0000), so raster edges don't get a rim of computed, off-brand inks."""
        best, best_de = None, BRAND_TOLERANCE
        for brand in self._fixed:
            de = delta_e_rgb(rgb, brand)
            if de <= best_de:
                best, best_de = brand, de
        return best

    def _neutral_k(self, level: int) -> float:
        """K for a grey: the K whose printed colour is closest to that grey in this profile.
        (A K-only ramp is far from linear because of dot gain; plain 1 - v/255 is ~12 dE off for dark greys.)"""
        if level >= 255:
            return 0.0
        if level <= 0:
            return 100.0
        return float(min(range(101), key=lambda k: self._delta_e((level,) * 3, (0, 0, 0, k))))

    def _delta_e(self, rgb: tuple[int, int, int], cmyk: tuple[float, float, float, float]) -> float:
        back = ImageCms.applyTransform(
            Image.new("CMYK", (1, 1), tuple(round(v / 100 * 255) for v in cmyk)), self._to_rgb
        ).getpixel((0, 0))
        return round(delta_e_rgb(rgb, back), 2)

    def convert01(self, r: float, g: float, b: float) -> tuple[float, float, float, float]:
        """PDF operands (0-1 floats) -> CMYK operands (0-1)."""
        conv = self.convert(*(round(min(max(v, 0.0), 1.0) * 255) for v in (r, g, b)))
        return tuple(v / 100 for v in conv.cmyk)  # type: ignore[return-value]

    @property
    def conversions(self) -> list[Conversion]:
        return list(self._cache.values())

    def warnings(self) -> list[str]:
        out = []
        for conv in self.conversions:
            if sum(conv.cmyk) > TOTAL_INK_LIMIT:
                out.append(f"{conv.label()} total ink {sum(conv.cmyk):g}% exceeds {TOTAL_INK_LIMIT:g}%")
        return out

    # ------------------------------------------------------------------- pixels
    def convert_image(self, image: Image.Image) -> Image.Image:
        """RGB image -> CMYK image using the same per-colour rules as flat colours."""
        import numpy as np

        rgb = np.asarray(image.convert("RGB"), dtype=np.uint8)
        packed = (rgb[..., 0].astype(np.uint32) << 16) | (rgb[..., 1].astype(np.uint32) << 8) | rgb[..., 2]
        uniq, inverse = np.unique(packed, return_inverse=True)
        table = np.zeros((len(uniq), 4), dtype=np.uint8)
        for i, value in enumerate(uniq):
            conv = self.convert(int(value >> 16) & 255, int(value >> 8) & 255, int(value) & 255)
            table[i] = [round(v / 100 * 255) for v in conv.cmyk]
        out = table[inverse.reshape(packed.shape)]
        return Image.fromarray(out, "CMYK")
