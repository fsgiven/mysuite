"""Cutout: remove the background from photos, locally (macOS Vision) — a transparent PNG beside each original.

A form over `mysuite cutout …`; see mysuite.tui.shell. Stable ids: `#input-files`, `#overwrite`, `#recursive`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from textual.app import ComposeResult
from textual.widgets import Checkbox, Collapsible, Label, Static

from mysuite.convert._parsing import SOURCE_EXTENSIONS
from mysuite.tui.shell import FormError, ToolScreen
from mysuite.tui.widgets.file_target import FileTarget

_SOURCES = frozenset(SOURCE_EXTENSIONS)


class CutoutScreen(ToolScreen):
    TOOL_KEY = "cutout"
    CLI = ("cutout",)
    HEADING = "Cutout"
    PERSIST = ("overwrite", "recursive")

    def compose_form(self) -> ComposeResult:
        yield Label("Photos", classes="section")
        yield FileTarget(
            input_id="input-files", browse_id="browse-input-files", extensions=_SOURCES, noun="photo",
            placeholder="photo.jpg, a folder, or a glob like ~/Pictures/*.jpg",
        )
        yield Checkbox("Include subfolders", id="recursive")
        yield Static("The subject is found on your Mac (Vision); nothing is uploaded. Result: <name>_cutout.png.", classes="hint")
        with Collapsible(title="Safety", collapsed=True, id="adv-safety"):
            yield Checkbox("Replace files that already exist", id="overwrite")

    def argv(self) -> list[str]:
        res = self.query_one(FileTarget).resolve()
        if res.empty:
            raise FormError("choose at least one photo — paste a path, drop a file, or use Files… / Folder…", "input-files")
        if res.missing or res.unsupported or res.empty_folders:
            bad = (res.missing or [str(p) for p in res.unsupported] or [str(p) for p in res.empty_folders])[0]
            raise FormError(f"not usable: {bad}" if res.missing or res.unsupported else f"no photos in {bad}", "input-files")
        out: list[str] = []
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
            return "nothing to do"
        lines = [f"{len(items)} cutout(s) — written beside the originals"]
        lines += [f"{Path(i['input']).name} → {Path(i['output']).name}" for i in items[:4] if i.get("output")]
        if len(items) > 4:
            lines.append(f"… and {len(items) - 4} more")
        return "\n".join(lines)
