"""Convert: any image/vector/PDF to another format, beside the original (or several formats at once).

A form over `mysuite convert …`; see mysuite.tui.shell. Stable ids: `#input-files`, `#target-format`, `#quality`, `#dpi`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Checkbox, Collapsible, Input, Label

from mysuite.convert._parsing import SOURCE_EXTENSIONS, TARGET_EXTENSIONS
from mysuite.tui.shell import ChipGroup, FormError, ToolScreen, field
from mysuite.tui.widgets.file_target import FileTarget

TARGETS = sorted(TARGET_EXTENSIONS)
_SOURCES = frozenset(SOURCE_EXTENSIONS)


class ConvertScreen(ToolScreen):
    TOOL_KEY = "convert"
    CLI = ("convert",)
    HEADING = "Convert"
    PERSIST = ("target-format", "quality", "dpi", "background", "overwrite", "recursive")

    def compose_form(self) -> ComposeResult:
        yield Label("Files to convert", classes="section")
        yield FileTarget(
            input_id="input-files", browse_id="browse-input-files", extensions=_SOURCES, noun="file",
            placeholder="photo.png, a folder, or a glob like ~/Pictures/*.heic",
        )
        yield Checkbox("Include subfolders", id="recursive")

        yield Label("Convert to", classes="section")
        yield field("Pick one or several (each gets its own file)", ChipGroup(TARGETS, id="target-format", columns=5))

        yield Label("Options", classes="section")
        with Horizontal(classes="pair"):
            yield field("Quality 0-100 (jpeg, webp)", Input(placeholder="default", id="quality"))
            yield field("DPI (svg, pdf, eps sources)", Input(placeholder="300", id="dpi"))
        yield field("Background for flattened transparency", Input(placeholder="white or #ffffff (jpeg and bmp default to white)", id="background"))
        with Collapsible(title="Safety", collapsed=True, id="adv-safety"):
            yield Checkbox("Replace files that already exist", id="overwrite")

    def apply_defaults(self) -> None:
        self.query_one("#target-format", ChipGroup).select_only(["png"])

    def _number(self, wid: str, label: str, *, whole: bool, low: float, high: float | None = None) -> str | None:
        raw = self.query_one(f"#{wid}", Input).value.strip()
        if not raw:
            return None
        try:
            value = int(raw) if whole else float(raw)
        except ValueError:
            raise FormError(f"{label} must be a {'whole ' if whole else ''}number, not {raw!r}", wid) from None
        if value < low or (high is not None and value > high):
            raise FormError(f"{label} must be {f'{low:g}-{high:g}' if high is not None else f'at least {low:g}'}", wid)
        return raw

    def argv(self) -> list[str]:
        res = self.query_one(FileTarget).resolve()
        if res.empty:
            raise FormError("choose at least one file — paste a path, drop a file, or use Files… / Folder…", "input-files")
        if res.missing or res.unsupported or res.empty_folders:
            bad = (res.missing or [str(p) for p in res.unsupported] or [str(p) for p in res.empty_folders])[0]
            raise FormError(f"not usable: {bad}" if res.missing or res.unsupported else f"no convertible files in {bad}", "input-files")
        targets = self.query_one("#target-format", ChipGroup).selected
        if not targets:
            raise FormError("choose at least one format to convert to", "target-format")
        out = ["--to", ",".join(targets)]

        def opt(flag: str, value: str | None) -> None:
            if value:
                out.extend([flag, value])

        opt("--quality", self._number("quality", "Quality", whole=True, low=0, high=100))
        opt("--dpi", self._number("dpi", "DPI", whole=False, low=1))
        opt("--background", self.query_one("#background", Input).value.strip() or None)
        if self.query_one("#recursive", Checkbox).value:
            out.append("--recursive")
        if self.query_one("#overwrite", Checkbox).value:
            out.append("--overwrite")
        if any(a.startswith("-") for a in res.args):
            return [*out, "--", *res.args]
        return [*res.args, *out]

    def output_dir(self) -> Path | None:
        for item in self.last_report.get("items", []):
            if item.get("output"):
                return Path(item["output"]).parent
        return None

    def plan_text(self, report: dict[str, Any]) -> str:
        items = report.get("items", [])
        if not items:
            return "nothing to convert"
        lines = [f"{len(items)} conversion(s) — files are written beside the originals"]
        for item in items[:4]:
            lines.append(f"{Path(item['input']).name} → {Path(item['output']).name}")
        if len(items) > 4:
            lines.append(f"… and {len(items) - 4} more")
        return "\n".join(lines)
