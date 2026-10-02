"""Watermark: stamp a logo (image or SVG) onto photos — a copy beside each original, originals untouched.

A form over `mysuite watermark …`; see mysuite.tui.shell. Stable ids: `#input-files`, `#logo`, `#position`, `#scale`,
`#opacity`, `#margin`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Checkbox, Collapsible, Input, Label, Select

from mysuite.convert._parsing import SOURCE_EXTENSIONS
from mysuite.tui.shell import FormError, ToolScreen, field
from mysuite.tui.widgets.file_target import FileTarget
from mysuite.watermark.watermark import GRAVITY_BY_POSITION

_SOURCES = frozenset(SOURCE_EXTENSIONS)
_BLANK = (None, Select.BLANK, Select.NULL)


class WatermarkScreen(ToolScreen):
    TOOL_KEY = "watermark"
    CLI = ("watermark",)
    HEADING = "Watermark"
    PERSIST = ("logo", "position", "scale", "opacity", "margin", "overwrite", "recursive")

    def compose_form(self) -> ComposeResult:
        yield Label("Photos", classes="section")
        yield FileTarget(
            input_id="input-files", browse_id="browse-input-files", extensions=_SOURCES, noun="photo",
            placeholder="photo.jpg, a folder, or a glob like ~/Pictures/*.jpg",
        )
        yield Checkbox("Include subfolders", id="recursive")

        yield Label("Logo", classes="section")
        yield FileTarget(
            input_id="logo", browse_id="browse-logo", extensions=_SOURCES, noun="logo",
            placeholder="logo.png or logo.svg", recent_kind="logos", folders=False,
        )

        yield Label("Placement", classes="section")
        with Horizontal(classes="pair"):
            yield field("Position", Select[str]([(p, p) for p in GRAVITY_BY_POSITION], id="position", prompt="bottom-right"), classes="grow")
            yield field("Opacity 0-100", Input(placeholder="80", id="opacity"), classes="narrow")
        with Horizontal(classes="pair"):
            yield field("Logo size, % of photo width", Input(placeholder="15", id="scale"))
            yield field("Margin, % of photo width", Input(placeholder="3", id="margin"))
        with Collapsible(title="Safety", collapsed=True, id="adv-safety"):
            yield Checkbox("Replace files that already exist", id="overwrite")

    def _number(self, wid: str, label: str, *, whole: bool, low: float, high: float | None = None) -> str | None:
        raw = self.query_one(f"#{wid}", Input).value.strip()
        if not raw:
            return None
        try:
            value = int(raw) if whole else float(raw)
        except ValueError:
            raise FormError(f"{label} must be a {'whole ' if whole else ''}number, not {raw!r}", wid) from None
        if value < low or (high is not None and value > high):
            raise FormError(f"{label} must be {f'{low:g}-{high:g}' if high is not None else f'above {low:g}'}", wid)
        return raw

    def argv(self) -> list[str]:
        res = self._targets()[0].resolve()
        logo = self._targets()[1].resolve()
        if res.empty:
            raise FormError("choose at least one photo — paste a path, drop a file, or use Files… / Folder…", "input-files")
        if res.missing or res.unsupported or res.empty_folders:
            bad = (res.missing or [str(p) for p in res.unsupported] or [str(p) for p in res.empty_folders])[0]
            raise FormError(f"not usable: {bad}" if res.missing or res.unsupported else f"no photos in {bad}", "input-files")
        if logo.empty:
            raise FormError("choose a logo — a PNG, JPEG or SVG to stamp on", "logo")
        if len(logo.files) != 1 or logo.missing or logo.unsupported:
            bad = (logo.missing or [str(p) for p in logo.unsupported] or ["more than one file"])[0]
            raise FormError(f"the logo must be one image file: {bad}", "logo")
        out = ["--logo", logo.args[0]]
        position = self.query_one("#position", Select).value
        if position not in _BLANK:
            out += ["--position", str(position)]
        for flag, wid, label, whole, low, high in (
            ("--scale", "scale", "Logo size", False, 0.1, 100),
            ("--opacity", "opacity", "Opacity", True, 0, 100),
            ("--margin", "margin", "Margin", False, 0, 50),
        ):
            value = self._number(wid, label, whole=whole, low=low, high=high)
            if value:
                out += [flag, value]
        if self.query_one("#recursive", Checkbox).value:
            out.append("--recursive")
        if self.query_one("#overwrite", Checkbox).value:
            out.append("--overwrite")
        if any(a.startswith("-") for a in res.args):
            return [*out, "--", *res.args]
        return [*res.args, *out]

    def _targets(self) -> list[FileTarget]:
        return list(self.query(FileTarget))

    def output_dir(self) -> Path | None:
        for item in self.last_report.get("items", []):
            if item.get("output"):
                return Path(item["output"]).parent
        return None

    def plan_text(self, report: dict[str, Any]) -> str:
        items = report.get("items", [])
        if not items:
            return "nothing to do"
        lines = [f"{len(items)} watermarked copy(ies) — written beside the originals"]
        lines += [f"{Path(i['input']).name} → {Path(i['output']).name}" for i in items[:4] if i.get("output")]
        if len(items) > 4:
            lines.append(f"… and {len(items) - 4} more")
        return "\n".join(lines)
