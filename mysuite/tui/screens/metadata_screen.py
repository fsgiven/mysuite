from __future__ import annotations

from pathlib import Path

from textual import events, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.timer import Timer
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
from textual.worker import get_current_worker

from mysuite.config import Config, load_config
from mysuite.convert._parsing import InvalidInputError, SOURCE_EXTENSIONS, resolve_input_files
from mysuite.metadata._parsing import output_path_for
from mysuite.metadata.metadata import MetadataOutcome, credit_file, strip_file
from mysuite.tui.dragdrop import merge_paths_into_input, parse_dropped_paths, parse_input_files_field
from mysuite.tui.screens.file_picker import FilePickerScreen
from mysuite.utils.subprocess_utils import MysuiteToolError

_DROP_EXTENSIONS = frozenset(SOURCE_EXTENSIONS)
_MODES = ["strip", "credit"]


class MetadataDropInput(Input):
    """Same drag-drop mechanism as ConvertDropInput/CutoutDropInput/SvgDropInput
    — see export_screen.SvgDropInput's docstring for why _on_paste needs
    event.prevent_default(), not just event.stop()."""

    def _on_paste(self, event: events.Paste) -> None:
        dropped = parse_dropped_paths(event.text, extensions=_DROP_EXTENSIONS)
        if dropped:
            self.value = merge_paths_into_input(self.value, dropped)
            event.stop()
            event.prevent_default()


class MetadataScreen(Screen):
    BINDINGS = [
        ("escape", "go_back", "Back"),
        ("f5", "preview", "Preview"),
        ("ctrl+r", "run", "Run"),
    ]

    _PREVIEW_MAX_FILES = 6
    _PREVIEW_DEBOUNCE_SECONDS = 0.4

    def __init__(self) -> None:
        super().__init__()
        self._config: Config | None = None
        self._preview_debounce_timer: Timer | None = None
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
                        yield MetadataDropInput(placeholder="path/to/photo or a folder", id="input-files")
                        yield Button("Browse", id="browse-input-files")

                    yield Label("Mode")
                    yield Select[str](
                        [("Strip — remove all metadata", "strip"), ("Credit — embed provenance", "credit")],
                        id="mode", allow_blank=False, value="strip",
                    )

                with Vertical(id="group-credit", classes="field-group"):
                    yield Label("Author")
                    yield Input(placeholder="Jane Doe", id="author")
                    yield Label("Copyright notice (optional)")
                    yield Input(placeholder="© 2026 Jane Doe", id="copyright")
                    yield Label("Generator")
                    yield Input(placeholder="mysuite", id="generator")

                with Vertical(id="group-options", classes="field-group"):
                    yield Checkbox("Overwrite existing files", id="overwrite")
                    yield Checkbox("Recursive (for folder inputs)", id="recursive")

                with Horizontal(classes="field-row", id="action-row"):
                    yield Button("Preview  [f5]", id="preview-btn")
                    yield Button("Run  [ctrl+r]", id="run-btn", variant="primary")

            with Vertical(id="results-pane"):
                with Vertical(id="group-preview", classes="field-group"):
                    yield RichLog(id="preview-list", markup=True, wrap=True)
                with Vertical(id="group-run", classes="field-group"):
                    yield ProgressBar(id="run-progress", total=100)
                    yield RichLog(id="run-log", markup=True, wrap=True)
                    yield Static("", id="run-summary")
        yield Footer()

    def on_mount(self) -> None:
        self.app.sub_title = "Metadata"
        self.query_one("#group-source", Vertical).border_title = "Source"
        self.query_one("#group-credit", Vertical).border_title = "Crediting"
        self.query_one("#group-options", Vertical).border_title = "Options"
        self.query_one("#group-preview", Vertical).border_title = "Preview"
        self.query_one("#group-run", Vertical).border_title = "Progress"

        self._config = load_config()
        self._update_mode_visibility()
        self.query_one("#input-files", Input).focus()

    def _update_mode_visibility(self) -> None:
        mode = self.query_one("#mode", Select).value
        self.query_one("#group-credit", Vertical).display = mode == "credit"

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "mode":
            self._update_mode_visibility()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "browse-input-files":
            self._browse_input_files()
        elif event.button.id == "preview-btn":
            self.action_preview()
        elif event.button.id == "run-btn":
            self.action_run()

    def _browse_input_files(self) -> None:
        def apply(path: Path | None) -> None:
            if path is not None:
                self.query_one("#input-files", Input).value = str(path)

        self.app.push_screen(FilePickerScreen(start_path=Path("/"), title="Choose input file"), apply)

    def _log(self, message: str) -> None:
        self.query_one("#run-log", RichLog).write(message)

    def _flash_error(self, field_id: str, message: str) -> None:
        field = self.query_one(field_id, Input)
        field.add_class("field-error")
        field.focus()
        self._log(f"[#F87171]✗[/#F87171] {message}")

    def _resolve_form(
        self,
    ) -> tuple[list[Path], str, str, str | None, str, bool] | None:
        for field_id in ("#input-files", "#author"):
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

        mode = self.query_one("#mode", Select).value
        if mode in (None, Select.BLANK, Select.NULL):
            mode = "strip"

        author = self.query_one("#author", Input).value.strip()
        if mode == "credit" and not author:
            self._flash_error("#author", "enter an author name for Credit mode")
            return None

        copyright_notice = self.query_one("#copyright", Input).value.strip() or None
        generator = self.query_one("#generator", Input).value.strip() or "mysuite"
        overwrite = self.query_one("#overwrite", Checkbox).value

        return files, mode, author, copyright_notice, generator, overwrite

    def _render_preview_list(self, files: list[Path], mode: str) -> None:
        preview_list = self.query_one("#preview-list", RichLog)
        preview_list.clear()
        output_mode = "stripped" if mode == "strip" else "credited"
        shown = files[: self._PREVIEW_MAX_FILES]
        for f in shown:
            out = output_path_for(f, output_mode)
            preview_list.write(f"{f.name} [dim]->[/dim] {out.name}")
        extra = len(files) - len(shown)
        if extra > 0:
            preview_list.write(f"[dim]+{extra} more[/dim]")
        self.query_one("#run-progress", ProgressBar).update(total=len(files), progress=0)

    def action_preview(self) -> None:
        resolved = self._resolve_form()
        if resolved is None:
            return
        files, mode, *_ = resolved
        self._render_preview_list(files, mode)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "input-files":
            return
        if self._preview_debounce_timer is not None:
            self._preview_debounce_timer.stop()
        self._preview_debounce_timer = self.set_timer(
            self._PREVIEW_DEBOUNCE_SECONDS, self._debounced_preview_refresh
        )

    def _debounced_preview_refresh(self) -> None:
        self._preview_debounce_timer = None
        value = self.query_one("#input-files", Input).value.strip()
        if not value:
            self.query_one("#preview-list", RichLog).clear()
            return
        raw_inputs = parse_input_files_field(value)
        recursive = self.query_one("#recursive", Checkbox).value
        try:
            files = resolve_input_files(raw_inputs, recursive=recursive)
        except InvalidInputError:
            self.query_one("#preview-list", RichLog).clear()
            return
        mode = self.query_one("#mode", Select).value
        if mode in (None, Select.BLANK, Select.NULL):
            mode = "strip"
        self._render_preview_list(files, mode)

    def action_run(self) -> None:
        resolved = self._resolve_form()
        if resolved is None:
            return
        files, mode, author, copyright_notice, generator, overwrite = resolved
        self.action_preview()

        assert self._config is not None
        self.query_one("#run-btn", Button).disabled = True
        self.query_one("#run-progress", ProgressBar).update(total=len(files), progress=0)
        self.query_one("#run-summary", Static).update("")

        self._run_metadata_worker(files, mode, author, copyright_notice, generator, overwrite)

    @work(thread=True, exclusive=True, group="metadata-run")
    def _run_metadata_worker(
        self,
        files: list[Path],
        mode: str,
        author: str,
        copyright_notice: str | None,
        generator: str,
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
                if mode == "credit":
                    outcome = credit_file(
                        input_path, author=author, copyright_notice=copyright_notice,
                        generator=generator, tools=self._config.tools, overwrite=overwrite,
                    )
                else:
                    outcome = strip_file(input_path, tools=self._config.tools, overwrite=overwrite)
            except MysuiteToolError as exc:
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
        self, outcome: MetadataOutcome | None, error: str | None, input_path: Path
    ) -> None:
        self.query_one("#run-progress", ProgressBar).advance(1)
        if error is not None:
            self._log(f"[#F87171]✗[/#F87171] {input_path}: {error}")
        elif outcome is not None and outcome.status == "skipped_existing":
            self._log(f"[dim]— exists, skipped: {outcome.output_path}[/dim]")
        elif outcome is not None:
            self._log(f"[#4ADE80]✓[/#4ADE80] {outcome.output_path}")

    def _on_run_complete(self, written: int, skipped: int, failed: int) -> None:
        self.query_one("#run-btn", Button).disabled = False
        self.last_summary_text = f"done — {written} written, {skipped} already existed, {failed} failed"
        self.query_one("#run-summary", Static).update(self.last_summary_text)

    def on_unmount(self) -> None:
        if self._preview_debounce_timer is not None:
            self._preview_debounce_timer.stop()
