from __future__ import annotations

from pathlib import Path

from rich.markup import escape
from textual import events, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Checkbox, Footer, Header, Input, Label, ProgressBar, RichLog, Select, Static
from textual.worker import get_current_worker

from mysuite.config import Config, load_config
from mysuite.convert._parsing import InvalidInputError, SOURCE_EXTENSIONS, resolve_input_files
from mysuite.transform.transform import FORMATS, GRAVITY, Ops, TransformError, TransformOutcome, transform_file
from mysuite.tui.dragdrop import merge_paths_into_input, parse_dropped_paths, parse_input_files_field
from mysuite.tui.screens.file_picker import FilePickerScreen
from mysuite.utils.paths import show_path
from mysuite.utils.subprocess_utils import MysuiteToolError

_DROP_EXTENSIONS = frozenset(SOURCE_EXTENSIONS)
_NONE = "none"


class TransformDropInput(Input):
    """Same drag-drop mechanism as the other tools' inputs."""

    def _on_paste(self, event: events.Paste) -> None:
        dropped = parse_dropped_paths(event.text, extensions=_DROP_EXTENSIONS)
        if dropped:
            self.value = merge_paths_into_input(self.value, dropped)
            event.stop()
            event.prevent_default()


def _select(options: list[str], *, id: str, value: str = _NONE) -> Select[str]:
    return Select[str]([(o, o) for o in options], id=id, allow_blank=False, value=value)


class TransformScreen(Screen):
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
            with VerticalScroll(id="form-pane"):
                with Vertical(id="group-source", classes="field-group"):
                    yield Label("File(s), a folder, or comma-separated — click here, then drag a file in")
                    with Horizontal(classes="field-row"):
                        yield TransformDropInput(placeholder="path/to/image or a folder", id="input-files")
                        yield Button("Browse", id="browse-input-files")
                    yield Checkbox("Overwrite existing files", id="overwrite")
                    yield Checkbox("Recursive (for folder inputs)", id="recursive")

                with Vertical(id="group-edits", classes="field-group"):
                    yield Label("Order: trim, crop, rotate, flip, resize, pad, round, background")
                    yield Checkbox("Trim the uniform border", id="trim")
                    with Horizontal(classes="field-row"):
                        with Vertical(classes="field-half"):
                            yield Label("Crop to aspect (16:9, 1:1 …)")
                            yield Input(placeholder="1:1", id="crop-aspect")
                        with Vertical(classes="field-half"):
                            yield Label("Crop box (WxH+X+Y)")
                            yield Input(placeholder="800x600+100+50", id="crop")
                    with Horizontal(classes="field-row"):
                        with Vertical(classes="field-half"):
                            yield Label("Rotate (clockwise)")
                            yield _select([_NONE, "90", "180", "270"], id="rotate")
                        with Vertical(classes="field-half"):
                            yield Label("Flip")
                            yield _select([_NONE, "horizontal", "vertical", "both"], id="flip")
                    with Horizontal(classes="field-row"):
                        with Vertical(classes="field-half"):
                            yield Label("Resize (512 · x512 · 512x512 · 50%)")
                            yield Input(placeholder="512", id="resize")
                        with Vertical(classes="field-half"):
                            yield Label("Resize mode")
                            yield _select(["fit", "fill", "exact"], id="resize-mode", value="fit")
                    with Horizontal(classes="field-row"):
                        with Vertical(classes="field-half"):
                            yield Label("Pad to (1:1 or 1200x630)")
                            yield Input(placeholder="1:1", id="pad")
                        with Vertical(classes="field-half"):
                            yield Label("Pad colour (empty = transparent)")
                            yield Input(placeholder="#ffffff", id="pad-color")
                    with Horizontal(classes="field-row"):
                        with Vertical(classes="field-half"):
                            yield Label("Round corners (24 or 50%)")
                            yield Input(placeholder="50%", id="round")
                        with Vertical(classes="field-half"):
                            yield Label("Flatten on colour")
                            yield Input(placeholder="#ffffff", id="background")
                    with Horizontal(classes="field-row"):
                        with Vertical(classes="field-half"):
                            yield Label("Anchor / part to keep")
                            yield _select(list(GRAVITY), id="gravity", value="center")
                        with Vertical(classes="field-half"):
                            yield Label("Output format")
                            yield _select(["same as source", *FORMATS], id="format", value="same as source")
                    yield Checkbox("Never enlarge (shrink only)", id="shrink-only")

                with Horizontal(classes="field-row", id="action-row"):
                    yield Button("Run  [ctrl+r]", id="run-btn", variant="primary")

            with Vertical(id="results-pane"):
                with Vertical(id="group-run", classes="field-group"):
                    yield ProgressBar(id="run-progress", total=100)
                    yield RichLog(id="run-log", markup=True, wrap=True)
                    yield Static("", id="run-summary")
        yield Footer()

    def on_mount(self) -> None:
        self.app.sub_title = "Transform"
        self.query_one("#group-source", Vertical).border_title = "Source"
        self.query_one("#group-edits", Vertical).border_title = "Edits"
        self.query_one("#group-run", Vertical).border_title = "Progress"
        self._config = load_config()
        self.query_one("#input-files", Input).focus()

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "browse-input-files":
            def apply(path: Path | None) -> None:
                if path is not None:
                    self.query_one("#input-files", Input).value = str(path)

            self.app.push_screen(FilePickerScreen(title="Choose input image"), apply)
        elif event.button.id == "run-btn":
            self.action_run()

    def _log(self, message: str) -> None:
        self.query_one("#run-log", RichLog).write(message)

    def _flash_error(self, field_id: str, message: str) -> None:
        field = self.query_one(field_id)
        field.add_class("field-error")
        field.focus()
        self._log(f"[#F87171]✗[/#F87171] {escape(message)}")

    def _text(self, field_id: str) -> str | None:
        return self.query_one(field_id, Input).value.strip() or None

    def _choice(self, field_id: str, none_value: str) -> str | None:
        value = self.query_one(field_id, Select).value
        return None if value in (None, Select.BLANK, Select.NULL, none_value) else str(value)

    def _resolve_form(self) -> tuple[list[Path], Ops, bool] | None:
        for field_id in ("#input-files", "#crop-aspect", "#crop", "#resize", "#pad", "#pad-color", "#round", "#background"):
            self.query_one(field_id).remove_class("field-error")
        value = self.query_one("#input-files", Input).value.strip()
        if not value:
            self._flash_error("#input-files", "enter an input file path, folder, or comma-separated list")
            return None
        try:
            files = resolve_input_files(parse_input_files_field(value), recursive=self.query_one("#recursive", Checkbox).value)
        except InvalidInputError as exc:
            self._flash_error("#input-files", str(exc))
            return None

        rotate = self._choice("#rotate", _NONE)
        ops = Ops(
            trim=self.query_one("#trim", Checkbox).value,
            crop=self._text("#crop"), crop_aspect=self._text("#crop-aspect"),
            gravity=self._choice("#gravity", "") or "center",
            rotate=float(rotate) if rotate else None, flip=self._choice("#flip", _NONE),
            resize=self._text("#resize"), resize_mode=self._choice("#resize-mode", "") or "fit",
            shrink_only=self.query_one("#shrink-only", Checkbox).value,
            pad=self._text("#pad"), pad_color=self._text("#pad-color"), round=self._text("#round"),
            background=self._text("#background"), format=self._choice("#format", "same as source"),
        )
        try:
            ops.validate()
            if not (ops.active() or ops.format):
                raise TransformError("nothing to do - set at least one edit or an output format")
        except TransformError as exc:
            message = str(exc)
            field = next((f"#{n.replace('_', '-')}" for n in ("crop", "crop_aspect", "resize", "pad", "pad_color", "round", "background")
                          if message.startswith(n) or f" {n} " in f" {message} "), "#input-files")
            self._flash_error(field if self.query(field) else "#input-files", message)
            return None
        return files, ops, self.query_one("#overwrite", Checkbox).value

    def action_run(self) -> None:
        resolved = self._resolve_form()
        if resolved is None:
            return
        files, ops, overwrite = resolved
        self.query_one("#run-btn", Button).disabled = True
        self.query_one("#run-progress", ProgressBar).update(total=len(files), progress=0)
        self.query_one("#run-summary", Static).update("")
        self._run_worker(files, ops, overwrite)

    @work(thread=True, exclusive=True, group="transform-run")
    def _run_worker(self, files: list[Path], ops: Ops, overwrite: bool) -> None:
        worker = get_current_worker()
        assert self._config is not None
        written = skipped = failed = 0
        for input_path in files:
            if worker.is_cancelled:
                break
            try:
                outcome = transform_file(input_path, ops, tools=self._config.tools, overwrite=overwrite)
            except (TransformError, MysuiteToolError) as exc:
                failed += 1
                self.app.call_from_thread(self._on_item_done, None, str(exc), input_path)
                continue
            if outcome.status == "skipped_existing":
                skipped += 1
            else:
                written += 1
            self.app.call_from_thread(self._on_item_done, outcome, None, input_path)
        self.app.call_from_thread(self._on_run_complete, written, skipped, failed)

    def _on_item_done(self, outcome: TransformOutcome | None, error: str | None, input_path: Path) -> None:
        self.query_one("#run-progress", ProgressBar).advance(1)
        if error is not None:
            self._log(f"[#F87171]✗[/#F87171] {show_path(input_path)}: {escape(error)}")
        elif outcome is not None and outcome.status == "skipped_existing":
            self._log(f"[dim]— exists, skipped: {show_path(outcome.output_path)}[/dim]")
        elif outcome is not None:
            self._log(f"[#4ADE80]✓[/#4ADE80] {show_path(outcome.output_path)} "
                      f"[dim]{outcome.input_size[0]}x{outcome.input_size[1]} → {outcome.output_size[0]}x{outcome.output_size[1]}[/dim]")
            for note in outcome.notes:
                self._log(f"[#FBBF24]⚠[/#FBBF24] {escape(note)}")

    def _on_run_complete(self, written: int, skipped: int, failed: int) -> None:
        self.query_one("#run-btn", Button).disabled = False
        self.last_summary_text = f"done — {written} written, {skipped} already existed, {failed} failed"
        self.query_one("#run-summary", Static).update(self.last_summary_text)
