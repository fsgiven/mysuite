from __future__ import annotations

from pathlib import Path

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
from mysuite.config import Config, load_config
from mysuite.convert._parsing import (
    InvalidInputError,
    SOURCE_EXTENSIONS,
    detect_source_format,
    resolve_input_files,
)
from mysuite.tui.dragdrop import merge_paths_into_input, parse_dropped_paths, parse_input_files_field
from mysuite.tui.screens.file_picker import FilePickerScreen
from mysuite.utils.subprocess_utils import MysuiteToolError
from mysuite.watermark.watermark import (
    GRAVITY_BY_POSITION,
    WatermarkError,
    WatermarkOutcome,
    watermark_file,
)

_DROP_EXTENSIONS = frozenset(SOURCE_EXTENSIONS)


class WatermarkDropInput(Input):
    """Same drag-drop mechanism as ConvertDropInput/CutoutDropInput/SvgDropInput
    — see export_screen.SvgDropInput's docstring for why _on_paste needs
    event.prevent_default(), not just event.stop()."""

    def _on_paste(self, event: events.Paste) -> None:
        dropped = parse_dropped_paths(event.text, extensions=_DROP_EXTENSIONS)
        if dropped:
            self.value = merge_paths_into_input(self.value, dropped)
            event.stop()
            event.prevent_default()


class WatermarkScreen(Screen):
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
                        "File(s), a folder, or comma-separated — "
                        "click here, then drag a file in to add it"
                    )
                    with Horizontal(classes="field-row"):
                        yield WatermarkDropInput(placeholder="path/to/photo or a folder", id="input-files")
                        yield Button("Browse", id="browse-input-files")

                    yield Label("Logo — image or SVG to stamp on")
                    with Horizontal(classes="field-row"):
                        yield Input(placeholder="path/to/logo.png", id="logo")
                        yield Button("Browse", id="browse-logo")

                    yield Label("Position")
                    yield Select[str](
                        [(pos, pos) for pos in GRAVITY_BY_POSITION],
                        id="position", allow_blank=False, value="bottom-right",
                    )

                    with Horizontal(classes="field-row"):
                        with Vertical(classes="field-half"):
                            yield Label("Scale % of width")
                            yield Input(placeholder="15", id="scale")
                        with Vertical(classes="field-half"):
                            yield Label("Opacity 0-100")
                            yield Input(placeholder="80", id="opacity")
                    yield Label("Margin % of width")
                    yield Input(placeholder="3", id="margin")

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
        self.app.sub_title = "Watermark"
        self.query_one("#group-source", Vertical).border_title = "Source"
        self.query_one("#group-run", Vertical).border_title = "Progress"

        self._config = load_config()
        self.query_one("#input-files", Input).focus()

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "browse-input-files":
            self._browse_input_files()
        elif event.button.id == "browse-logo":
            self._browse_logo()
        elif event.button.id == "run-btn":
            self.action_run()

    def _browse_input_files(self) -> None:
        def apply(path: Path | None) -> None:
            if path is not None:
                self.query_one("#input-files", Input).value = str(path)

        self.app.push_screen(FilePickerScreen(title="Choose input photo"), apply)

    def _browse_logo(self) -> None:
        def apply(path: Path | None) -> None:
            if path is not None:
                self.query_one("#logo", Input).value = str(path)

        self.app.push_screen(FilePickerScreen(title="Choose logo"), apply)

    def _log(self, message: str) -> None:
        self.query_one("#run-log", RichLog).write(message)

    def _flash_error(self, field_id: str, message: str) -> None:
        field = self.query_one(field_id, Input)
        field.add_class("field-error")
        field.focus()
        self._log(f"[#F87171]✗[/#F87171] {escape(message)}")

    def _resolve_form(
        self,
    ) -> tuple[list[Path], Path, str, float, int, float, bool] | None:
        for field_id in ("#input-files", "#logo", "#scale", "#opacity", "#margin"):
            self.query_one(field_id, Input).remove_class("field-error")

        value = self.query_one("#input-files", Input).value.strip()
        if not value:
            self._flash_error(
                "#input-files", "enter an input file path, folder, or comma-separated list"
            )
            return None
        raw_inputs = parse_input_files_field(value)
        recursive = self.query_one("#recursive", Checkbox).value
        try:
            files = resolve_input_files(raw_inputs, recursive=recursive)
        except InvalidInputError as exc:
            self._flash_error("#input-files", str(exc))
            return None

        logo_str = self.query_one("#logo", Input).value.strip()
        if not logo_str:
            self._flash_error("#logo", "choose a logo file")
            return None
        logo_path = Path(logo_str)
        if detect_source_format(logo_path) is None:
            self._flash_error("#logo", f"unrecognized logo format: {logo_path}")
            return None

        position = self.query_one("#position", Select).value
        if position in (None, Select.BLANK, Select.NULL):
            position = "bottom-right"

        scale_str = self.query_one("#scale", Input).value.strip()
        try:
            scale = float(scale_str) if scale_str else 15.0
        except ValueError:
            self._flash_error("#scale", f"invalid scale: {scale_str!r}")
            return None

        opacity_str = self.query_one("#opacity", Input).value.strip()
        try:
            opacity = int(opacity_str) if opacity_str else 80
            if not (0 <= opacity <= 100):
                raise ValueError
        except ValueError:
            self._flash_error("#opacity", f"opacity must be 0-100: {opacity_str!r}")
            return None

        margin_str = self.query_one("#margin", Input).value.strip()
        try:
            margin = float(margin_str) if margin_str else 3.0
        except ValueError:
            self._flash_error("#margin", f"invalid margin: {margin_str!r}")
            return None

        overwrite = self.query_one("#overwrite", Checkbox).value
        return files, logo_path, position, scale, opacity, margin, overwrite

    def action_run(self) -> None:
        resolved = self._resolve_form()
        if resolved is None:
            return
        files, logo_path, position, scale, opacity, margin, overwrite = resolved

        assert self._config is not None
        self.query_one("#run-btn", Button).disabled = True
        self.query_one("#run-progress", ProgressBar).update(total=len(files), progress=0)
        self.query_one("#run-summary", Static).update("")

        self._run_watermark_worker(files, logo_path, position, scale, opacity, margin, overwrite)

    @work(thread=True, exclusive=True, group="watermark-run")
    def _run_watermark_worker(
        self,
        files: list[Path],
        logo_path: Path,
        position: str,
        scale: float,
        opacity: int,
        margin: float,
        overwrite: bool,
    ) -> None:
        worker = get_current_worker()
        assert self._config is not None
        written = 0
        skipped = 0
        failed = 0
        for input_path in files:
            if worker.is_cancelled:
                break
            try:
                outcome = watermark_file(
                    input_path, logo_path=logo_path, tools=self._config.tools,
                    position=position, scale_pct=scale, opacity=opacity, margin_pct=margin,
                    overwrite=overwrite,
                )
            except (WatermarkError, MysuiteToolError) as exc:
                failed += 1
                self.app.call_from_thread(self._on_item_done, None, str(exc), input_path)
                continue
            if outcome.status == "skipped_existing":
                skipped += 1
            else:
                written += 1
            self.app.call_from_thread(self._on_item_done, outcome, None, input_path)
        self.app.call_from_thread(self._on_run_complete, written, skipped, failed)

    def _on_item_done(
        self, outcome: WatermarkOutcome | None, error: str | None, input_path: Path
    ) -> None:
        self.query_one("#run-progress", ProgressBar).advance(1)
        if error is not None:
            self._log(f"[#F87171]✗[/#F87171] {show_path(input_path)}: {escape(error)}")
        elif outcome is not None and outcome.status == "skipped_existing":
            self._log(f"[dim]— exists, skipped: {show_path(outcome.output_path)}[/dim]")
        elif outcome is not None:
            self._log(f"[#4ADE80]✓[/#4ADE80] {show_path(outcome.output_path)}")

    def _on_run_complete(self, written: int, skipped: int, failed: int) -> None:
        self.query_one("#run-btn", Button).disabled = False
        self.last_summary_text = f"done — {written} written, {skipped} already existed, {failed} failed"
        self.query_one("#run-summary", Static).update(self.last_summary_text)
