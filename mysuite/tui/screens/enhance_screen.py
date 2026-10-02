"""Enhance: upscale, denoise, sharpen, white balance, scratch repair — locally, a copy beside each photo.

A form over `mysuite enhance run …`; see mysuite.tui.shell. Stable ids: `#input-files`, `#preset`, `#backend`, `#scale`,
`#denoise`, `#sharpen`, `#saturation`, `#contrast`, `#gamma`, `#restore-scratches`, `#auto-white-balance`, `#face-enhance`,
`#output-format`, `#output-quality`. A preset only fills the form; the command lists `--preset NAME` plus whatever you
changed on top of it.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Checkbox, Collapsible, Input, Label, Select

from mysuite.config import load_config
from mysuite.enhance._parsing import ENHANCE_EXTENSIONS
from mysuite.enhance.upscalers import backend_status
from mysuite.tui.shell import FormError, ToolScreen, field
from mysuite.tui.widgets.file_target import FileTarget

_SOURCES = frozenset(ENHANCE_EXTENSIONS)
_BLANK = (None, Select.BLANK, Select.NULL)

# preset/settings key -> (widget kind, value shown when a preset doesn't set it)
_FIELDS: dict[str, tuple[str, object]] = {
    "scale": ("input", ""), "denoise": ("input", ""), "sharpen": ("input", ""),
    "saturation": ("input", ""), "contrast": ("input", ""), "gamma": ("input", ""),
    "output_quality": ("input", ""),
    "face_enhance": ("checkbox", False), "auto_white_balance": ("checkbox", True),
    "restore_scratches": ("checkbox", False),
    "output_format": ("select", "png"),
}
# key -> (flag, label, whole number?, low, high)
_NUMBERS: dict[str, tuple[str, str, bool, float, float]] = {
    "scale": ("--scale", "Scale", True, 1, 8),
    "denoise": ("--denoise", "Denoise", False, 0, 1),
    "sharpen": ("--sharpen", "Sharpen", False, 0, 1),
    "saturation": ("--saturation", "Saturation", False, 0, 3),
    "contrast": ("--contrast", "Contrast", False, 0, 3),
    "gamma": ("--gamma", "Gamma", False, 0.2, 3),
    "output_quality": ("--quality", "Quality", True, 1, 100),
}
_SWITCHES = {
    "face_enhance": ("--face-enhance", "--no-face-enhance"),
    "auto_white_balance": ("--auto-white-balance", "--no-auto-white-balance"),
    "restore_scratches": ("--restore-scratches", "--no-restore-scratches"),
}


def _wid(key: str) -> str:
    return key.replace("_", "-")


class EnhanceScreen(ToolScreen):
    TOOL_KEY = "enhance"
    CLI = ("enhance", "run")
    HEADING = "Enhance"
    PERSIST = ("backend", *(_wid(k) for k in _FIELDS), "overwrite", "recursive")

    def __init__(self) -> None:
        super().__init__()
        self._presets: dict[str, dict] = {}
        self._baseline: dict[str, object] = {k: d for k, (_, d) in _FIELDS.items()}
        self._preset_name = ""

    def compose_form(self) -> ComposeResult:
        yield Label("Photos", classes="section")
        yield FileTarget(
            input_id="input-files", browse_id="browse-input-files", extensions=_SOURCES, noun="photo",
            placeholder="photo.jpg, a folder, or a glob like ~/Pictures/*.jpg",
        )
        yield Checkbox("Include subfolders", id="recursive")

        yield Label("Recipe", classes="section")
        with Horizontal(classes="pair"):
            yield field("Preset (fills the form)", Select[str]([("(none — gentle defaults)", "")], id="preset", allow_blank=False, value=""), classes="grow")
            yield field("Backend", Select[str](
                [("auto (AI if installed)", "auto"), ("classical", "classical"), ("realesrgan (AI)", "realesrgan")],
                id="backend", allow_blank=False, value="auto"), classes="grow")
        with Horizontal(classes="pair"):
            yield field("Scale 1-8", Input(placeholder="1", id="scale"))
            yield field("Denoise 0-1", Input(placeholder="0", id="denoise"))
            yield field("Sharpen 0-1", Input(placeholder="0", id="sharpen"))
        with Horizontal(classes="pair"):
            yield field("Saturation 0-3", Input(placeholder="1.0", id="saturation"))
            yield field("Contrast 0-3", Input(placeholder="1.0", id="contrast"))
            yield field("Gamma 0.2-3", Input(placeholder="1.0", id="gamma"))
        yield Checkbox("Auto white balance", id="auto-white-balance", value=True)
        yield Checkbox("Restore scratches (thin straight lines)", id="restore-scratches")
        yield Checkbox("Face enhance (needs the AI backend)", id="face-enhance")

        yield Label("Result", classes="section")
        with Horizontal(classes="pair"):
            yield field("Format", Select[str]([("png", "png"), ("jpg", "jpg"), ("webp", "webp")], id="output-format", allow_blank=False, value="png"), classes="narrow")
            yield field("Quality 1-100 (jpg, webp)", Input(placeholder="95", id="output-quality"))
        with Collapsible(title="Safety", collapsed=True, id="adv-safety"):
            yield Checkbox("Replace files that already exist", id="overwrite")

    def prepare(self) -> None:
        self._presets = load_config().enhance_presets
        self.query_one("#preset", Select).set_options(
            [("(none — gentle defaults)", "")] + [(n, n) for n in sorted(self._presets)]
        )

    def on_mount(self) -> None:
        if not backend_status()["realesrgan_available"]:
            self.write_log("[dim]AI backend not installed — using the classical backend[/dim]")

    # ------------------------------------------------------------------------ preset
    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "preset":
            self._apply_preset(str(event.value) if event.value not in _BLANK else "")

    def _set_field(self, key: str, value: object) -> None:
        kind, _ = _FIELDS[key]
        widget = self.query_one(f"#{_wid(key)}")
        if kind == "checkbox":
            widget.value = bool(value)
        elif kind == "select":
            widget.value = value
        else:
            widget.value = "" if value == "" else str(value)

    def _apply_preset(self, name: str) -> None:
        self._preset_name = name
        settings = self._presets.get(name) if name else {}
        if settings is None:
            return
        self._baseline = {k: d for k, (_, d) in _FIELDS.items()}
        for key, value in settings.items():
            if key in _FIELDS:
                self._baseline[key] = value
        for key in _FIELDS:
            self._set_field(key, self._baseline[key])

    # ------------------------------------------------------------------------ command
    def _same_as_baseline(self, key: str, current: object) -> bool:
        base = self._baseline.get(key, _FIELDS[key][1])
        if isinstance(current, bool):
            return current == bool(base)
        try:
            return float(current) == float(base)           # "2" and 2 and 2.0 are the same number
        except (TypeError, ValueError):
            return str(current) == str(base)

    def argv(self) -> list[str]:
        res = self.query_one(FileTarget).resolve()
        if res.empty:
            raise FormError("choose at least one photo — paste a path, drop a file, or use Files… / Folder…", "input-files")
        if res.missing or res.unsupported or res.empty_folders:
            bad = (res.missing or [str(p) for p in res.unsupported] or [str(p) for p in res.empty_folders])[0]
            raise FormError(f"not usable: {bad}" if res.missing or res.unsupported else f"no photos in {bad}", "input-files")
        out: list[str] = []
        if self._preset_name:
            out += ["--preset", self._preset_name]
        for key, (flag, label, whole, low, high) in _NUMBERS.items():
            raw = self.query_one(f"#{_wid(key)}", Input).value.strip()
            if not raw:
                continue
            try:
                value = int(raw) if whole else float(raw)
            except ValueError:
                raise FormError(f"{label} must be a {'whole ' if whole else ''}number, not {raw!r}", _wid(key)) from None
            if not low <= value <= high:
                raise FormError(f"{label} must be {low:g}-{high:g}", _wid(key))
            if not self._same_as_baseline(key, raw):
                out += [flag, raw]
        for key, (on, off) in _SWITCHES.items():
            now = self.query_one(f"#{_wid(key)}", Checkbox).value
            if not self._same_as_baseline(key, now):
                out.append(on if now else off)
        fmt = self.query_one("#output-format", Select).value
        if fmt not in _BLANK and not self._same_as_baseline("output_format", fmt):
            out += ["--format", str(fmt)]
        backend = self.query_one("#backend", Select).value
        if backend not in _BLANK and backend != "auto":
            out += ["--backend", str(backend)]
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
        items = [i for i in report.get("items", []) if i.get("output")]
        if not items:
            return "nothing to do"
        lines = [f"{len(items)} photo(s) — enhanced copies are written beside the originals"]
        lines += [f"{Path(i['input']).name} → {Path(i['output']).name}" for i in items[:4]]
        if len(items) > 4:
            lines.append(f"… and {len(items) - 4} more")
        return "\n".join(lines)
