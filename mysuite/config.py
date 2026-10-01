from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

from mysuite.compress.presets import BUILT_IN_COMPRESS_PRESETS
from mysuite.enhance.presets import BUILT_IN_ENHANCE_PRESETS
from mysuite.export.naming import DEFAULT_BUNDLE_NAMING_TEMPLATE


class MysuiteConfigError(RuntimeError):
    pass


DEFAULT_NAMING_TEMPLATE = "{name}_{size}{ext}"
DEFAULT_PATH_TEMPLATE = "{format}/{colorspace}/{naming}"

# Ready-to-use presets available even without a mysuite.toml. A user-defined
# preset of the same name (in [presets.NAME]) overrides the built-in one.
BUILT_IN_PRESETS: dict[str, dict] = {
    "favicon": {
        "sizes": [16, 32, 48],
        "formats": ["ico", "png"],
        "profiles": ["rgb"],
        "out_dir": "exports/favicon",
    },
    "macos-icon": {
        # icns always builds the fixed standard Apple iconset regardless of
        # --sizes (see planner.ICNS_STANDARD_PIXEL_SIZES) — this entry is just
        # for a sensible out_dir/formats default.
        "formats": ["icns"],
        "profiles": ["rgb"],
        "out_dir": "exports/macos-icon",
    },
}


@dataclass
class ExportSettings:
    out_dir: str = "exports"
    sizes: list[str | int] = field(default_factory=lambda: [16, 32, 64, 128, 256, 512, 1024])
    formats: list[str] = field(default_factory=lambda: ["png", "pdf"])
    profiles: list[str] = field(default_factory=lambda: ["rgb"])
    dpi: float = 300.0
    default_unit: str = "px"
    """Unit assumed for a size with no explicit suffix (px, mm, cm, or in)."""
    naming_template: str = DEFAULT_NAMING_TEMPLATE
    path_template: str = DEFAULT_PATH_TEMPLATE
    bundle_naming_template: str = DEFAULT_BUNDLE_NAMING_TEMPLATE
    strict: bool = False
    normalize_png: bool = True
    overwrite: bool = False
    background: str | None = None
    quality: int | None = None
    padding: str | None = None
    """Uniform margin baseline applied to any side not set individually below."""
    margin_top: str | None = None
    margin_right: str | None = None
    margin_bottom: str | None = None
    margin_left: str | None = None
    png_compression: int | None = None
    date_stamp: bool = False
    """Insert today's date (YYYYMMDD) into filenames using the tool's own default
    naming templates. A naming_template you've customized is left alone."""
    variant: str | None = None
    """Nests this export under exports/<name>/<variant>/... alongside the primary
    exports/<name>/... export, instead of getting its own top-level name folder."""
    recolor: dict[str, str] = field(default_factory=dict)
    """Colour substitutions ({"#000000": "#ffffff"}) applied to the SVG source before
    rendering. Colours are matched by value (#d00 = #dd0000 = rgb(221,0,0) = hsl(...))
    and, within recolor_tolerance (CIEDE2000), near-identical colours too."""
    recolor_tolerance: float = 2.0
    cmyk_mode: str = "exact"
    """'exact' (the profile's numbers), 'clean' or 'clean:<step>' (snap each channel to a
    multiple of step, default 5: 73/92 -> 75/90)."""
    cmyk_profile: str | None = None
    """ICC profile used for RGB->CMYK and embedded as the PDF output intent. Default:
    Ghostscript's CMYK SWOP profile (else macOS Generic CMYK)."""


@dataclass
class ToolPaths:
    rsvg_convert: str = "rsvg-convert"
    gs: str = "gs"
    magick: str = "magick"
    iconutil: str = "iconutil"
    cutout_tool: str = "mysuite-cutout"
    exiftool: str = "exiftool"
    c2patool: str = "c2patool"
    cjpeg: str = "/opt/homebrew/opt/mozjpeg/bin/cjpeg"
    """mozjpeg is keg-only — a bare "cjpeg" on PATH resolves to plain
    jpeg-turbo's weaker encoder instead, so this defaults to mozjpeg's own
    keg path rather than relying on the user linking/PATH-ing it themselves."""
    cwebp: str = "cwebp"
    avifenc: str = "avifenc"
    oxipng: str = "oxipng"
    pngquant: str = "pngquant"
    gifsicle: str = "gifsicle"


@dataclass
class Config:
    export: ExportSettings = field(default_factory=ExportSettings)
    tools: ToolPaths = field(default_factory=ToolPaths)
    presets: dict[str, dict] = field(default_factory=lambda: dict(BUILT_IN_PRESETS))
    compress_presets: dict[str, dict] = field(
        default_factory=lambda: dict(BUILT_IN_COMPRESS_PRESETS)
    )
    enhance_presets: dict[str, dict] = field(
        default_factory=lambda: dict(BUILT_IN_ENHANCE_PRESETS)
    )

    def resolve_export_settings(self, preset: str | None) -> ExportSettings:
        settings = self.export
        if preset is not None:
            if preset not in self.presets:
                raise MysuiteConfigError(
                    f"unknown preset {preset!r}; available presets: "
                    f"{', '.join(sorted(self.presets)) or '(none defined)'}"
                )
            settings = replace(settings, **self.presets[preset])
        return settings

    def resolve_compress_settings(self, preset: str | None, overrides: dict) -> dict:
        """Layers overrides (explicitly-passed CLI flags or TUI field values —
        anything not None) on top of preset's settings bundle, if given. A
        preset is a starting point, not a lock: any field also present in
        overrides wins over the preset's value for that field."""
        return _layer_preset(self.compress_presets, "compress", preset, overrides)

    def resolve_enhance_settings(self, preset: str | None, overrides: dict) -> dict:
        """Same layering as resolve_compress_settings, for enhance presets."""
        return _layer_preset(self.enhance_presets, "enhance", preset, overrides)


def _layer_preset(presets: dict[str, dict], kind: str, preset: str | None, overrides: dict) -> dict:
    base: dict = {}
    if preset is not None:
        if preset not in presets:
            raise MysuiteConfigError(
                f"unknown {kind} preset {preset!r}; available presets: "
                f"{', '.join(sorted(presets)) or '(none defined)'}"
            )
        base = dict(presets[preset])
    base.update({k: v for k, v in overrides.items() if v is not None})
    return base


def _find_config_path(explicit: Path | None) -> Path | None:
    if explicit is not None:
        if not explicit.is_file():
            raise MysuiteConfigError(f"config file not found: {explicit}")
        return explicit
    cwd_config = Path.cwd() / "mysuite.toml"
    if cwd_config.is_file():
        return cwd_config
    home_config = Path.home() / ".config" / "mysuite" / "mysuite.toml"
    if home_config.is_file():
        return home_config
    return None


def load_config(explicit_path: Path | None = None) -> Config:
    path = _find_config_path(explicit_path)
    if path is None:
        return Config()

    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        raise MysuiteConfigError(f"malformed TOML in {path}: {exc}") from exc

    export_data = data.get("export", {})
    try:
        export_settings = ExportSettings(**export_data)
    except TypeError as exc:
        raise MysuiteConfigError(f"invalid [export] settings in {path}: {exc}") from exc

    tools_data = data.get("tools", {})
    try:
        tool_paths = ToolPaths(**tools_data)
    except TypeError as exc:
        raise MysuiteConfigError(f"invalid [tools] settings in {path}: {exc}") from exc

    presets = {**BUILT_IN_PRESETS, **data.get("presets", {})}
    compress_presets = {**BUILT_IN_COMPRESS_PRESETS, **data.get("compress_presets", {})}
    enhance_presets = {**BUILT_IN_ENHANCE_PRESETS, **data.get("enhance_presets", {})}

    return Config(
        export=export_settings, tools=tool_paths, presets=presets,
        compress_presets=compress_presets, enhance_presets=enhance_presets,
    )
