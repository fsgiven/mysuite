from __future__ import annotations

# Ready-to-use enhance presets, available even without a mysuite.toml. Each is
# a starting point: any field also passed explicitly (CLI flag or TUI field)
# overrides it. A user-defined preset of the same name in
# [enhance_presets.NAME] in mysuite.toml overrides the built-in one.
#
# Fields: scale (1-8), denoise/sharpen (0-1), face_enhance (needs the optional
# AI backend; no-op otherwise), auto_white_balance, saturation/contrast
# (1.0 = unchanged), gamma (1.0 = unchanged, <1 brightens), restore_scratches,
# output_format (png/jpg/webp), output_quality (jpg/webp, 1-100).
BUILT_IN_ENHANCE_PRESETS: dict[str, dict] = {
    "prime": {
        "scale": 4, "denoise": 0.5, "sharpen": 0.6, "face_enhance": True,
        "auto_white_balance": True, "saturation": 1.1, "contrast": 1.05, "gamma": 1.0,
        "restore_scratches": False, "output_format": "png", "output_quality": 95,
    },
    "gentle": {
        "scale": 2, "denoise": 0.15, "sharpen": 0.25, "face_enhance": False,
        "auto_white_balance": False, "saturation": 1.0, "contrast": 1.0, "gamma": 1.0,
        "restore_scratches": False, "output_format": "png", "output_quality": 95,
    },
    "old-photo": {
        "scale": 2, "denoise": 0.7, "sharpen": 0.4, "face_enhance": True,
        "auto_white_balance": True, "saturation": 1.0, "contrast": 1.05, "gamma": 0.95,
        "restore_scratches": True, "output_format": "png", "output_quality": 95,
    },
    "ai-art": {
        "scale": 2, "denoise": 0.3, "sharpen": 0.5, "face_enhance": False,
        "auto_white_balance": False, "saturation": 1.05, "contrast": 1.0, "gamma": 1.0,
        "restore_scratches": False, "output_format": "png", "output_quality": 95,
    },
    "portrait": {
        "scale": 2, "denoise": 0.3, "sharpen": 0.3, "face_enhance": True,
        "auto_white_balance": True, "saturation": 1.02, "contrast": 1.02, "gamma": 1.0,
        "restore_scratches": False, "output_format": "jpg", "output_quality": 92,
    },
}

# Defaults for any field neither a preset nor an explicit flag sets.
ENHANCE_DEFAULTS: dict = {
    "scale": 2, "denoise": 0.3, "sharpen": 0.3, "face_enhance": False,
    "auto_white_balance": True, "saturation": 1.0, "contrast": 1.0, "gamma": 1.0,
    "restore_scratches": False, "output_format": "png", "output_quality": 95,
}
