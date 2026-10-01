from __future__ import annotations

# Ready-to-use compress presets, available even without a mysuite.toml. Each
# is a full settings bundle (which codec, plus that codec's own parameters) —
# a starting point, not a lock: any field also passed explicitly (CLI flag or
# TUI field) overrides the preset's value for that field, matching how
# mysuite.config.BUILT_IN_PRESETS work for export. A user-defined preset of
# the same name (in [compress_presets.NAME] in mysuite.toml) overrides the
# built-in one.
BUILT_IN_COMPRESS_PRESETS: dict[str, dict] = {
    "web-photo-balanced": {
        "codec": "mozjpeg", "quality": 82, "progressive": True, "subsample": "4:2:0",
    },
    "web-photo-max-quality": {
        "codec": "mozjpeg", "quality": 95, "progressive": True, "subsample": "4:4:4",
    },
    "web-photo-small": {
        "codec": "mozjpeg", "quality": 60, "progressive": True, "subsample": "4:2:0",
    },
    "modern-web-webp": {
        "codec": "webp", "quality": 82, "method": 6, "alpha_quality": 100,
    },
    "modern-web-avif": {
        "codec": "avif", "quality": 55, "speed": 6,
    },
    "lossless-archive": {
        "codec": "oxipng", "effort": 6, "interlace": False,
    },
    "icon-graphic": {
        "codec": "pngquant", "quality_range": "80-95", "pngquant_speed": 1, "dither": True,
    },
    "gif-optimize": {
        "codec": "gifsicle", "optimize_level": 3, "lossy": 20,
    },
}
