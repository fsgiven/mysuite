from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

from rich.table import Table

from mysuite.config import ToolPaths
from mysuite.utils.console import console

_NATIVE_BUILD_HINT = "mysuite install cutout --yes  (builds the helper from source; macOS 14+ and the Xcode Command Line Tools)"


_VISION_BUILD_HINT = "mysuite install vision --yes  (builds the helper from source; macOS 13+ and the Xcode Command Line Tools)"


@dataclass(frozen=True)
class ToolSpec:
    attr: str
    install_hint: str  # a full instruction line, e.g. "brew install X" or a build pointer
    version_flag: str = "--version"  # exiftool's own convention is "-ver", not "--version"
    required: bool = False           # the core (export, convert, watermark, inspect, metadata strip) needs it


TOOL_SPECS: list[ToolSpec] = [
    ToolSpec("rsvg_convert", "brew install librsvg", required=True),
    ToolSpec("gs", "brew install ghostscript", required=True),
    ToolSpec("magick", "brew install imagemagick", required=True),
    ToolSpec("cutout_tool", _NATIVE_BUILD_HINT),
    ToolSpec("vision_tool", _VISION_BUILD_HINT),
    ToolSpec("exiftool", "brew install exiftool", version_flag="-ver", required=True),
    ToolSpec("c2patool", "brew install c2patool"),
    ToolSpec("pdftocairo", "brew install poppler  (or: mysuite install tools --yes)", version_flag="-v"),
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
    "metadata-declare": ("exiftool",),
    "svg-import": ("pdftocairo", "gs"),
    "svg-optimise": ("rsvg_convert",),
    "compress-mozjpeg": ("cjpeg",),
    "compress-webp": ("cwebp",),
    "compress-avif": ("avifenc",),
    "compress-oxipng": ("oxipng",),
    "compress-pngquant": ("pngquant",),
    "compress-gifsicle": ("gifsicle",),
}


# Commands that use a tool without refusing to start when it is missing (they fail on the files that need it).
ALSO_USED_BY: dict[str, tuple[str, ...]] = {
    "gs": ("pdf render", "pdf compress", "inspect (PDF/EPS)", "diff (PDF/EPS)"),
    "magick": ("inspect (PDF/EPS)",),
    "exiftool": ("inspect (GPS, camera)",),
    "rsvg_convert": ("inspect --thumb (SVG)", "diff (SVG)", "kit make"),
}


def _command_name(key: str) -> str:
    for prefix in ("compress-",):
        if key.startswith(prefix):
            return f"compress --codec {key[len(prefix):]}"
    return {"qr-read": "qr read"}.get(key, key.replace("-", " "))


def unlocks(attr: str) -> list[str]:
    """The commands a tool makes work, e.g. vision_tool -> ['ocr', 'qr read']."""
    names = [_command_name(k) for k, needed in NEEDS.items() if attr in needed]
    return list(dict.fromkeys(names + list(ALSO_USED_BY.get(attr, ()))))


def missing_tools(tools: ToolPaths, needed: tuple[str, ...] | None = None) -> list[str]:
    """Missing tools; restricted to `needed` (attr names) when given."""
    resolved = check_tools(tools)
    return [name for name, path in resolved.items() if path is None and (needed is None or name in needed)]


def run_doctor(tools: ToolPaths, needed: tuple[str, ...] | None = None) -> bool:
    """Prints a status table and returns True when every REQUIRED tool is present (optional ones may be missing).
    With `needed`, exactly those tools count as required (the rest are shown as optional)."""
    resolved = check_tools(tools)
    specs = {spec.attr: spec for spec in TOOL_SPECS}
    is_required = (lambda a: a in needed) if needed is not None else (lambda a: specs[a].required)

    table = Table(title="mysuite doctor")
    table.add_column("tool")
    table.add_column("kind")
    table.add_column("status")
    table.add_column("needed for", overflow="fold")
    table.add_column("path / version", overflow="fold")

    all_ok = True
    for attr in sorted(resolved, key=lambda a: (not is_required(a), list(specs).index(a))):
        path = resolved[attr]
        kind = "required" if is_required(attr) else "[dim]optional[/dim]"
        used = ", ".join(unlocks(attr))
        if path is None:
            if is_required(attr):
                all_ok = False
                table.add_row(attr, kind, "[#F87171]missing[/#F87171]", used, "-")
            else:
                table.add_row(attr, kind, "[#FBBF24]not installed[/#FBBF24]", used, "-")
        else:
            table.add_row(attr, kind, "[#4ADE80]ok[/#4ADE80]", used, f"{path}\n{_resolved_version(path, specs[attr].version_flag)}")

    console.print(table)

    missing_req = [a for a in resolved if resolved[a] is None and is_required(a)]
    missing_opt = [a for a in resolved if resolved[a] is None and not is_required(a)]
    if missing_req:
        console.print("\n[#F87171]Install the missing required tool(s):[/#F87171]")
        for attr in missing_req:
            console.print(f"  {attr}: {specs[attr].install_hint}")
    if missing_opt:
        console.print("\n[dim]Optional, install only if you need what they unlock:[/dim]")
        for attr in missing_opt:
            console.print(f"  [dim]{attr} ({', '.join(unlocks(attr))}): {specs[attr].install_hint}[/dim]")
    if not missing_req:
        console.print("\n[bold green]ok[/bold green] - every required tool is installed"
                      + (f"; {len(missing_opt)} optional one(s) missing" if missing_opt else ""))
    return all_ok


def doctor_report(tools: ToolPaths) -> bool:
    """`mysuite doctor`: JSON items (found, required, unlocks, install hint) plus the table. True = exit 0."""
    from mysuite.utils import jsonout

    resolved = check_tools(tools)
    for spec in TOOL_SPECS:
        path = resolved[spec.attr]
        jsonout.add_item(tool=spec.attr, found=path is not None, path=path, required=spec.required,
                         unlocks=unlocks(spec.attr), install_hint=None if path else spec.install_hint,
                         status="ok" if path else "missing")
    jsonout.set_extra(
        missing_required=[s.attr for s in TOOL_SPECS if s.required and resolved[s.attr] is None],
        missing_optional=[s.attr for s in TOOL_SPECS if not s.required and resolved[s.attr] is None],
    )
    return run_doctor(tools)


def require_tools(tools: ToolPaths, key: str) -> None:
    """Exit 4 if a command's tools are missing: one line per missing tool with how to install it (and, for --json,
    structured install hints). The full table is `mysuite doctor`'s job."""
    import typer
    from rich.markup import escape

    from mysuite.utils import jsonout

    needed = NEEDS[key]
    missing = missing_tools(tools, needed)
    if not missing:
        return
    hints = {spec.attr: spec.install_hint for spec in TOOL_SPECS}
    jsonout.add_error("missing required tool(s): " + ", ".join(missing))
    jsonout.set_extra(missing_tools=[{"tool": name, "install_hint": hints[name]} for name in missing])
    for name in missing:
        console.print(f"[#F87171]✗[/#F87171] {escape(key.replace('-', ' '))} needs {escape(name)}, which is not installed: "
                      f"{escape(hints[name])}", soft_wrap=True)
    raise typer.Exit(jsonout.EXIT_MISSING_TOOL)
