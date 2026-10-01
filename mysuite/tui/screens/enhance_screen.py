from __future__ import annotations

from pathlib import Path
from typing import Any

from textual import events, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import (
    Button,
    Checkbox,
    Footer,
    Header,
    Input,
    Label,
    ProgressBar,
    RichLog,
    Select,
    Static,
)
from rich.markup import escape
from textual.worker import get_current_worker

from mysuite.utils.paths import show_path
from mysuite.config import Config, MysuiteConfigError, load_config
from mysuite.enhance._parsing import (
    ENHANCE_EXTENSIONS,
    InvalidEnhanceInputError,
    check_no_output_collisions,
    resolve_input_files,
)
from mysuite.enhance.enhance import EnhanceError, EnhanceOutcome, EnhanceSettings, enhance_file
from mysuite.enhance.upscalers import backend_status
from mysuite.tui.dragdrop import merge_paths_into_input, parse_dropped_paths, parse_input_files_field
from mysuite.tui.screens.file_picker import FilePickerScreen

_DROP_EXTENSIONS = frozenset(ENHANCE_EXTENSIONS)

# preset/settings key -> (widget kind, default shown when a preset doesn't set it)
_FIELDS: dict[str, tuple[str, object]] = {
    "scale": ("input", ""), "denoise": ("input", ""), "sharpen": ("input", ""),
    "saturation": ("input", ""), "contrast": ("input", ""), "gamma": ("input", ""),
    "output_quality": ("input", ""),
    "face_enhance": ("checkbox", False), "auto_white_balance": ("checkbox", True),
    "restore_scratches": ("checkbox", False),
    "output_format": ("select", "png"),
}
_INT_FIELDS = {"scale", "output_quality"}
_LABELS = {
    "scale": "Scale 1-8", "denoise": "Denoise 0-1", "sharpen": "Sharpen 0-1",
    "saturation": "Saturation", "contrast": "Contrast", "gamma": "Gamma",
    "output_quality": "Quality 1-100",
}


class EnhanceDropInput(Input):
    """Same drag-drop mechanism as the other tools' drop inputs — see
    export_screen.SvgDropInput's docstring for why _on_paste needs
    event.prevent_default(), not just event.stop()."""

    def _on_paste(self, event: events.Paste) -> None:
        dropped = parse_dropped_paths(event.text, extensions=_DROP_EXTENSIONS)
        if dropped:
            self.value = merge_paths_into_input(self.value, dropped)
            event.stop()
            event.prevent_default()


class EnhanceScreen(Screen):
    BINDINGS = [
        ("escape", "go_back", "Back"),
        ("ctrl+r", "run", "Run"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._config: Config | None = None
        self.last_summary_text: str = ""

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with Vertical(id="form-pane"):
                with Vertical(id="group-source", classes="field-group"):
                    yield Label(
                        "Photo(s), a folder, or comma-separated — "
                        "click here, then drag a file in to add it"
                    )
                    with Horizontal(classes="field-row"):
                        yield EnhanceDropInput(placeholder="path/to/photo or a folder", id="input-files")
                        yield Button("Browse", id="browse-input-files")

                    yield Label("Preset — a starting point, still fully editable below")
                    yield Select[str]([("(none — gentle defaults)", "")], id="preset", allow_blank=False, value="")

                    yield Label("Backend")
                    yield Select[str](
                        [("auto (AI if installed, else classical)", "auto"), ("classical", "classical"),
                         ("realesrgan (AI, optional install)", "realesrgan")],
                        id="backend", allow_blank=False, value="auto",
                    )

                with Vertical(id="group-adjust", classes="field-group"):
                    with Horizontal(classes="field-row"):
                        for key in ("scale", "denoise", "sharpen"):
                            with Vertical(classes="field-half"):
                                yield Label(_LABELS[key])
                                yield Input(placeholder="", id=key.replace("_", "-"))
                    with Horizontal(classes="field-row"):
                        for key in ("saturation", "contrast", "gamma"):
                            with Vertical(classes="field-half"):
                                yield Label(_LABELS[key])
                                yield Input(placeholder="1.0", id=key)
                    yield Checkbox("Restore scratches (thin straight lines)", id="restore-scratches")
                    yield Checkbox("Auto white balance", id="auto-white-balance")
                    yield Checkbox("Face enhance (needs AI backend)", id="face-enhance")

                with Vertical(id="group-output", classes="field-group"):
                    with Horizontal(classes="field-row"):
                        with Vertical(classes="field-half"):
                            yield Label("Format")
                            yield Select[str](
                                [("png", "png"), ("jpg", "jpg"), ("webp", "webp")],
                                id="output-format", allow_blank=False, value="png",
                            )
                        with Vertical(classes="field-half"):
                            yield Label(_LABELS["output_quality"] + " (jpg/webp)")
                            yield Input(placeholder="95", id="output-quality")
                    yield Checkbox("Overwrite existing files", id="overwrite")
                    yield Checkbox("Recursive (for folder inputs)", id="recursive")

                with Horizontal(classes="field-row", id="action-row"):
                    yield Button("Run  [ctrl+r]", id="run-btn", variant="primary")

            with Vertical(id="results-pane"):
                with Vertical(id="group-run", classes="field-group"):
                    yield ProgressBar(id="run-progress", total=100)
                    yield RichLog(id="run-log", markup=True, wrap=True)
                    yield Static("", id="run-summary")
        yield Footer()

    def on_mount(self) -> None:
        self.app.sub_title = "Enhance"
        self.query_one("#group-source", Vertical).border_title = "Source"
        self.query_one("#group-adjust", Vertical).border_title = "Enhance"
        self.query_one("#group-output", Vertical).border_title = "Output"
        self.query_one("#group-run", Vertical).border_title = "Progress"

        self._config = load_config()
        self.query_one("#preset", Select).set_options(
            [("(none — gentle defaults)", "")] + [(n, n) for n in sorted(self._config.enhance_presets)]
        )
        if not backend_status()["realesrgan_available"]:
            self._log("[dim]AI backend not installed — classical backend only (see README)[/dim]")
        self.query_one("#input-files", Input).focus()

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "preset":
            self._apply_preset(event.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "browse-input-files":
            self._browse_input_files()
        elif event.button.id == "run-btn":
            self.action_run()

    def _browse_input_files(self) -> None:
        def apply(path: Path | None) -> None:
            if path is not None:
                self.query_one("#input-files", Input).value = str(path)

        self.app.push_screen(FilePickerScreen(title="Choose input photo"), apply)

    def _log(self, message: str) -> None:
        self.query_one("#run-log", RichLog).write(message)

    def _flash_error(self, field_id: str, message: str) -> None:
        field = self.query_one(field_id, Input)
        field.add_class("field-error")
        field.focus()
        self._log(f"[#F87171]✗[/#F87171] {escape(message)}")

    @staticmethod
    def _widget_id(key: str) -> str:
        return "#" + key.replace("_", "-")

    def _set_field(self, key: str, value: object) -> None:
        kind, _ = _FIELDS[key]
        widget_id = self._widget_id(key)
        if kind == "checkbox":
            self.query_one(widget_id, Checkbox).value = bool(value)
        elif kind == "select":
            self.query_one(widget_id, Select).value = value
        else:
            self.query_one(widget_id, Input).value = "" if value == "" else str(value)

    def _apply_preset(self, name: str | None) -> None:
        if not name or self._config is None:
            return
        settings = self._config.enhance_presets.get(name)
        if settings is None:
            return
        for key, (_, default) in _FIELDS.items():
            self._set_field(key, default)
        for key, value in settings.items():
            if key in _FIELDS:
                self._set_field(key, value)

    def _read_number(self, key: str) -> Any | None:
        """Blank -> None (preset/default applies). Invalid -> flashes the field
        and raises ValueError."""
        widget_id = self._widget_id(key)
        raw = self.query_one(widget_id, Input).value.strip()
        if not raw:
            return None
        try:
            return int(raw) if key in _INT_FIELDS else float(raw)
        except ValueError:
            kind = "a whole number" if key in _INT_FIELDS else "a number"
            self._flash_error(widget_id, f"{_LABELS[key]} must be {kind}: {raw!r}")
            raise

    def _resolve_form(self) -> tuple[list[Path], str, EnhanceSettings, bool] | None:
        error_ids = ["#input-files"] + [self._widget_id(k) for k in _INT_FIELDS | {
            "denoise", "sharpen", "saturation", "contrast", "gamma"}]
        for field_id in error_ids:
            self.query_one(field_id, Input).remove_class("field-error")

        value = self.query_one("#input-files", Input).value.strip()
        if not value:
            self._flash_error("#input-files", "enter a photo path, folder, or comma-separated list")
            return None
        try:
            files = resolve_input_files(parse_input_files_field(value),
                                        recursive=self.query_one("#recursive", Checkbox).value)
        except InvalidEnhanceInputError as exc:
            self._flash_error("#input-files", str(exc))
            return None

        explicit: dict[str, Any] = {}
        try:
            for key in ("scale", "denoise", "sharpen", "saturation", "contrast", "gamma", "output_quality"):
                explicit[key] = self._read_number(key)
        except ValueError:
            return None
        explicit["face_enhance"] = self.query_one("#face-enhance", Checkbox).value
        explicit["auto_white_balance"] = self.query_one("#auto-white-balance", Checkbox).value
        explicit["restore_scratches"] = self.query_one("#restore-scratches", Checkbox).value
        explicit["output_format"] = self.query_one("#output-format", Select).value

        try:
            assert self._config is not None
            settings = EnhanceSettings.from_dict(self._config.resolve_enhance_settings("gentle", explicit))
            check_no_output_collisions(files, settings.output_format)
        except (EnhanceError, InvalidEnhanceInputError, MysuiteConfigError) as exc:
            self._flash_error("#input-files", str(exc))
            return None

        backend = self.query_one("#backend", Select).value
        if backend in (None, Select.BLANK, Select.NULL):
            backend = "auto"
        return files, backend, settings, self.query_one("#overwrite", Checkbox).value

    def action_run(self) -> None:
        resolved = self._resolve_form()
        if resolved is None:
            return
        files, backend, settings, overwrite = resolved

        self.query_one("#run-btn", Button).disabled = True
        self.query_one("#run-progress", ProgressBar).update(total=len(files), progress=0)
        self.query_one("#run-summary", Static).update("")
        self._run_enhance_worker(files, backend, settings, overwrite)

    @work(thread=True, exclusive=True, group="enhance-run")
    def _run_enhance_worker(
        self, files: list[Path], backend: str, settings: EnhanceSettings, overwrite: bool
    ) -> None:
        worker = get_current_worker()
        written = skipped = failed = 0
        for input_path in files:
            if worker.is_cancelled:
                break
            try:
                outcome = enhance_file(input_path, settings, backend=backend, overwrite=overwrite)
            except EnhanceError as exc:
                failed += 1
                self.app.call_from_thread(self._on_item_done, None, str(exc), input_path)
                continue
            if outcome.status == "skipped_existing":
                skipped += 1
            else:
                written += 1
            self.app.call_from_thread(self._on_item_done, outcome, None, input_path)
        self.app.call_from_thread(self._on_run_complete, written, skipped, failed)

    def _on_item_done(self, outcome: EnhanceOutcome | None, error: str | None, input_path: Path) -> None:
        self.query_one("#run-progress", ProgressBar).advance(1)
        if error is not None:
            self._log(f"[#F87171]✗[/#F87171] {show_path(input_path)}: {escape(error)}")
        elif outcome is not None and outcome.status == "skipped_existing":
            self._log(f"[dim]— exists, skipped: {show_path(outcome.output_path)}[/dim]")
        elif outcome is not None:
            self._log(
                f"[#4ADE80]✓[/#4ADE80] {show_path(outcome.output_path)} [dim]{outcome.input_size[0]}x{outcome.input_size[1]}"
                f" -> {outcome.output_size[0]}x{outcome.output_size[1]}, {outcome.backend_used}[/dim]"
            )
            for note in outcome.notes:
                self._log(f"[#FBBF24]⚠[/#FBBF24] {escape(note)}")

    def _on_run_complete(self, written: int, skipped: int, failed: int) -> None:
        self.query_one("#run-btn", Button).disabled = False
        self.last_summary_text = f"done — {written} written, {skipped} already existed, {failed} failed"
        self.query_one("#run-summary", Static).update(self.last_summary_text)
