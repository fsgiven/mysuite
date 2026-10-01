from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

from rich.table import Table

from mysuite.config import ToolPaths
from mysuite.utils.console import console

_NATIVE_BUILD_HINT = (
    "mysuite/native/cutout/build.sh — this one isn't brew-installable, it's built from "
    "source (requires macOS 14+ and the Xcode Command Line Tools)"
)


_VISION_BUILD_HINT = (
    "mysuite/native/vision/build.sh /opt/homebrew/bin — text recognition (ocr) and QR reading; built from source "
    "(macOS 13+, Xcode Command Line Tools). Optional: only `ocr` and `qr read` need it"
)


@dataclass(frozen=True)
class ToolSpec:
    attr: str
    install_hint: str  # a full instruction line, e.g. "brew install X" or a build pointer
    version_flag: str = "--version"  # exiftool's own convention is "-ver", not "--version"


TOOL_SPECS: list[ToolSpec] = [
    ToolSpec("rsvg_convert", "brew install librsvg"),
    ToolSpec("gs", "brew install ghostscript"),
    ToolSpec("magick", "brew install imagemagick"),
    ToolSpec("cutout_tool", _NATIVE_BUILD_HINT),
    ToolSpec("vision_tool", _VISION_BUILD_HINT),
    ToolSpec("exiftool", "brew install exiftool", version_flag="-ver"),
    ToolSpec("c2patool", "brew install c2patool"),
    ToolSpec(
        "cjpeg",
        "brew install mozjpeg (keg-only — this tool points at its keg path directly, "
        "not PATH, so no linking step is needed)",
        version_flag="-version",
    ),
    ToolSpec("cwebp", "brew install webp", version_flag="-version"),
    ToolSpec("avifenc", "brew install libavif"),
    ToolSpec("oxipng", "brew install oxipng"),
    ToolSpec("pngquant", "brew install pngquant", version_flag="--version"),
    ToolSpec("gifsicle", "brew install gifsicle", version_flag="--version"),
]


def _resolved_version(binary_path: str, version_flag: str) -> str:
    try:
        result = subprocess.run(
            [binary_path, version_flag], capture_output=True, text=True, timeout=5
        )
        first_line = (result.stdout or result.stderr).strip().splitlines()
        return first_line[0] if first_line else "(unknown version)"
    except Exception:
        return "(unknown version)"


def check_tools(tools: ToolPaths) -> dict[str, str | None]:
    """Returns {attr_name: resolved_path_or_None} for each required tool."""
    return {spec.attr: shutil.which(getattr(tools, spec.attr)) for spec in TOOL_SPECS}


# What each command actually shells out to. A command only refuses to run when ITS
# tools are missing — previously every command required every tool, including the
# macOS-only cutout helper, so a fresh clone couldn't export a single file.
NEEDS: dict[str, tuple[str, ...]] = {
    "export": ("rsvg_convert", "gs", "magick"),
    "convert": ("rsvg_convert", "gs", "magick"),
    "watermark": ("rsvg_convert", "gs", "magick"),
    "cutout": ("cutout_tool", "rsvg_convert", "gs", "magick", "exiftool"),
    "ocr": ("vision_tool",),
    "qr-read": ("vision_tool",),
    "metadata-strip": ("exiftool", "magick"),
    "metadata-randomize": ("exiftool", "magick"),
    "metadata-credit": ("c2patool",),
    "compress-mozjpeg": ("cjpeg",),
    "compress-webp": ("cwebp",),
    "compress-avif": ("avifenc",),
    "compress-oxipng": ("oxipng",),
    "compress-pngquant": ("pngquant",),
    "compress-gifsicle": ("gifsicle",),
}


def missing_tools(tools: ToolPaths, needed: tuple[str, ...] | None = None) -> list[str]:
    """Missing tools; restricted to `needed` (attr names) when given."""
    resolved = check_tools(tools)
    return [name for name, path in resolved.items() if path is None and (needed is None or name in needed)]


def run_doctor(tools: ToolPaths, needed: tuple[str, ...] | None = None) -> bool:
    """Prints a status table. Returns True if all required tools are present. With
    `needed`, only those tools count as required: others missing are shown as
    optional and get no install nag."""
    resolved = check_tools(tools)

    table = Table(title="mysuite doctor")
    table.add_column("tool")
    table.add_column("status")
    table.add_column("path / version")

    version_flags_by_attr = {spec.attr: spec.version_flag for spec in TOOL_SPECS}
    all_ok = True
    for attr, path in resolved.items():
        if path is None:
            if needed is None or attr in needed:
                all_ok = False
                table.add_row(attr, "[#F87171]missing[/#F87171]", "-")
            else:
                table.add_row(attr, "[dim]not installed (optional here)[/dim]", "-")
        else:
            version = _resolved_version(path, version_flags_by_attr[attr])
            table.add_row(attr, "[#4ADE80]ok[/#4ADE80]", f"{path}\n{version}")

    console.print(table)

    if not all_ok:
        hints_by_attr = {spec.attr: spec.install_hint for spec in TOOL_SPECS}
        console.print(f"\n[#FBBF24]Install the missing tool(s):[/#FBBF24]")
        for attr in resolved:
            if resolved[attr] is None and (needed is None or attr in needed):
                console.print(f"  {attr}: {hints_by_attr[attr]}")

    return all_ok


def require_tools(tools: ToolPaths, key: str) -> None:
    """Exit 4 (with the doctor table and, for --json, structured install hints) if a command's tools are missing."""
    import typer

    from mysuite.utils import jsonout

    needed = NEEDS[key]
    missing = missing_tools(tools, needed)
    if not missing:
        return
    hints = {spec.attr: spec.install_hint for spec in TOOL_SPECS}
    jsonout.add_error("missing required tool(s): " + ", ".join(missing))
    jsonout.set_extra(missing_tools=[{"tool": name, "install_hint": hints[name]} for name in missing])
    run_doctor(tools, needed)
    raise typer.Exit(jsonout.EXIT_MISSING_TOOL)
