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
    SOURCE_EXTENSIONS,
    TARGET_EXTENSIONS,
    InvalidInputError,
    check_no_output_collisions,
    resolve_input_files,
)
from mysuite.convert.converter import ConversionError, ConvertOutcome, convert_file
from mysuite.tui.dragdrop import merge_paths_into_input, parse_dropped_paths, parse_input_files_field
from mysuite.tui.screens.file_picker import FilePickerScreen
from mysuite.utils.subprocess_utils import MysuiteToolError

_DROP_EXTENSIONS = frozenset(SOURCE_EXTENSIONS)


class ConvertDropInput(Input):
    """Same drag-drop mechanism as export_screen.SvgDropInput, but accepts any
    recognized convert source format instead of just .svg — see that class's
    docstring for why _on_paste needs event.prevent_default(), not just
    event.stop(), to suppress Input's own default paste-insertion."""

    def _on_paste(self, event: events.Paste) -> None:
        dropped = parse_dropped_paths(event.text, extensions=_DROP_EXTENSIONS)
        if dropped:
            self.value = merge_paths_into_input(self.value, dropped)
            event.stop()
            event.prevent_default()


class ConvertScreen(Screen):
    TOOL_KEY = "convert"

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
                        yield ConvertDropInput(placeholder="path/to/file or a folder", id="input-files")
                        yield Button("Browse", id="browse-input-files")

                    yield Label("Convert to")
                    yield Select[str](
                        [(fmt, fmt) for fmt in sorted(TARGET_EXTENSIONS)],
                        id="target-format", allow_blank=False, value="png",
                    )

                    with Horizontal(classes="field-row"):
                        with Vertical(classes="field-half"):
                            yield Label("Quality 0-100 (jpeg/webp)")
                            yield Input(placeholder="", id="quality")
                        with Vertical(classes="field-half"):
                            yield Label("DPI (vector source only)")
                            yield Input(placeholder="300", id="dpi")

                    yield Label("Background — e.g. white, #ffffff (jpeg/bmp default to white)")
                    yield Input(placeholder="", id="background")

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
        self.app.sub_title = "Convert"
        self.query_one("#group-source", Vertical).border_title = "Source"
        self.query_one("#group-run", Vertical).border_title = "Progress"

        self._config = load_config()
        self.query_one("#input-files", Input).focus()

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "browse-input-files":
            self._browse_input_files()
        elif event.button.id == "run-btn":
            self.action_run()

    def _browse_input_files(self) -> None:
        def apply(path: Path | None) -> None:
            if path is not None:
                self.query_one("#input-files", Input).value = str(path)

        self.app.push_screen(FilePickerScreen(title="Choose input file"), apply)

    def _log(self, message: str) -> None:
        self.query_one("#run-log", RichLog).write(message)

    def _flash_error(self, field_id: str, message: str) -> None:
        field = self.query_one(field_id, Input)
        field.add_class("field-error")
        field.focus()
        self._log(f"[#F87171]✗[/#F87171] {escape(message)}")

    def _resolve_form(
        self,
    ) -> tuple[list[Path], str, float, int | None, str | None, bool] | None:
        for field_id in ("#input-files", "#dpi", "#quality"):
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

        target_format = self.query_one("#target-format", Select).value
        if target_format in (None, Select.BLANK, Select.NULL):
            self._flash_error("#input-files", "choose a target format")
            return None

        try:
            check_no_output_collisions(files, target_format)
        except InvalidInputError as exc:
            self._flash_error("#input-files", str(exc))
            return None

        dpi_str = self.query_one("#dpi", Input).value.strip()
        try:
            dpi = float(dpi_str) if dpi_str else 300.0
        except ValueError:
            self._flash_error("#dpi", f"invalid dpi: {dpi_str!r}")
            return None

        quality_str = self.query_one("#quality", Input).value.strip()
        try:
            quality = int(quality_str) if quality_str else None
            if quality is not None and not (0 <= quality <= 100):
                raise ValueError
        except ValueError:
            self._flash_error("#quality", f"quality must be 0-100: {quality_str!r}")
            return None

        background = self.query_one("#background", Input).value.strip() or None
        overwrite = self.query_one("#overwrite", Checkbox).value

        return files, target_format, dpi, quality, background, overwrite

    def action_run(self) -> None:
        resolved = self._resolve_form()
        if resolved is None:
            return
        files, target_format, dpi, quality, background, overwrite = resolved

        assert self._config is not None
        self.query_one("#run-btn", Button).disabled = True
        self.query_one("#run-progress", ProgressBar).update(total=len(files), progress=0)
        self.query_one("#run-summary", Static).update("")

        self._run_convert_worker(files, target_format, dpi, quality, background, overwrite)

    @work(thread=True, exclusive=True, group="convert-run")
    def _run_convert_worker(
        self,
        files: list[Path],
        target_format: str,
        dpi: float,
        quality: int | None,
        background: str | None,
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
                outcome = convert_file(
                    input_path, target_format,
                    tools=self._config.tools, dpi=dpi, quality=quality,
                    background=background, overwrite=overwrite,
                )
            except (ConversionError, MysuiteToolError) as exc:
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
        self, outcome: ConvertOutcome | None, error: str | None, input_path: Path
    ) -> None:
        self.query_one("#run-progress", ProgressBar).advance(1)
        if error is not None:
            self._log(f"[#F87171]✗[/#F87171] {show_path(input_path)}: {escape(error)}")
        elif outcome is not None and outcome.status == "skipped_existing":
            self._log(f"[dim]— exists, skipped: {show_path(outcome.output_path)}[/dim]")
        elif outcome is not None:
            self._log(f"[#4ADE80]✓[/#4ADE80] {show_path(outcome.output_path)}")
            if outcome.note:
                self._log(f"[#FBBF24]⚠[/#FBBF24] {escape(outcome.note)}")

    def _on_run_complete(self, written: int, skipped: int, failed: int) -> None:
        self.query_one("#run-btn", Button).disabled = False
        self.last_summary_text = f"done — {written} written, {skipped} already existed, {failed} failed"
        self.query_one("#run-summary", Static).update(self.last_summary_text)
