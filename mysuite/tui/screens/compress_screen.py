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
from mysuite.compress._parsing import CODECS, InvalidCompressInputError, check_no_output_collisions
from mysuite.compress.compress import CompressError, CompressOutcome, compress_file
from mysuite.config import Config, load_config
from mysuite.convert._parsing import SOURCE_EXTENSIONS, InvalidInputError, resolve_input_files
from mysuite.tui.dragdrop import merge_paths_into_input, parse_dropped_paths, parse_input_files_field
from mysuite.tui.screens.file_picker import FilePickerScreen
from mysuite.utils.subprocess_utils import MysuiteToolError

_DROP_EXTENSIONS = frozenset(SOURCE_EXTENSIONS)

_CODEC_LABELS = [
    ("mozjpeg — JPEG", "mozjpeg"),
    ("cwebp — WebP", "webp"),
    ("avifenc — AVIF", "avif"),
    ("oxipng — PNG (lossless)", "oxipng"),
    ("pngquant — PNG (lossy)", "pngquant"),
    ("gifsicle — GIF", "gifsicle"),
]


class CompressDropInput(Input):
    """Same drag-drop mechanism as ConvertDropInput/WatermarkDropInput/etc.
    — see export_screen.SvgDropInput's docstring for why _on_paste needs
    event.prevent_default(), not just event.stop()."""

    def _on_paste(self, event: events.Paste) -> None:
        dropped = parse_dropped_paths(event.text, extensions=_DROP_EXTENSIONS)
        if dropped:
            self.value = merge_paths_into_input(self.value, dropped)
            event.stop()
            event.prevent_default()


class CompressScreen(Screen):
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
                        yield CompressDropInput(placeholder="path/to/photo or a folder", id="input-files")
                        yield Button("Browse", id="browse-input-files")

                    yield Label("Preset — a starting point, still fully editable below")
                    yield Select[str](
                        [("(none — set everything manually)", "")], id="preset", allow_blank=False, value="",
                    )

                    yield Label("Codec")
                    yield Select[str](_CODEC_LABELS, id="codec", allow_blank=False, value="mozjpeg")

                with Vertical(id="group-universal", classes="field-group"):
                    with Horizontal(classes="field-row"):
                        with Vertical(classes="field-half"):
                            yield Label("Quality 0-100 (mozjpeg/webp/avif)")
                            yield Input(placeholder="", id="quality")
                        with Vertical(classes="field-half"):
                            yield Label("Sharpen amount (unset = off)")
                            yield Input(placeholder="", id="sharpen-amount")
                    with Horizontal(classes="field-row"):
                        with Vertical(classes="field-half"):
                            yield Label("Sharpen radius")
                            yield Input(placeholder="2.0", id="sharpen-radius")
                        with Vertical(classes="field-half"):
                            yield Label("Sharpen sigma")
                            yield Input(placeholder="1.0", id="sharpen-sigma")
                    yield Label("Sharpen threshold")
                    yield Input(placeholder="0.0", id="sharpen-threshold")

                with Vertical(id="group-mozjpeg", classes="field-group"):
                    yield Checkbox("Progressive encoding", id="progressive", value=True)
                    yield Label("Chroma subsampling")
                    yield Select[str](
                        [("4:2:0 (smaller)", "4:2:0"), ("4:4:4 (sharper color)", "4:4:4")],
                        id="subsample", allow_blank=False, value="4:2:0",
                    )

                with Vertical(id="group-webp", classes="field-group"):
                    yield Checkbox("Lossless", id="lossless")
                    with Horizontal(classes="field-row"):
                        with Vertical(classes="field-half"):
                            yield Label("Method 0-6")
                            yield Input(placeholder="6", id="method")
                        with Vertical(classes="field-half"):
                            yield Label("Alpha quality 0-100")
                            yield Input(placeholder="100", id="alpha-quality")

                with Vertical(id="group-avif", classes="field-group"):
                    yield Label("Speed 0-10 (slower = smaller)")
                    yield Input(placeholder="6", id="speed")

                with Vertical(id="group-oxipng", classes="field-group"):
                    yield Label("Effort 0-6")
                    yield Input(placeholder="4", id="effort")
                    yield Checkbox("Interlace (Adam7)", id="interlace")

                with Vertical(id="group-pngquant", classes="field-group"):
                    with Horizontal(classes="field-row"):
                        with Vertical(classes="field-half"):
                            yield Label("Quality range")
                            yield Input(placeholder="65-90", id="quality-range")
                        with Vertical(classes="field-half"):
                            yield Label("Speed 1-11")
                            yield Input(placeholder="4", id="pngquant-speed")
                    yield Checkbox("Dither (Floyd-Steinberg)", id="dither", value=True)

                with Vertical(id="group-gifsicle", classes="field-group"):
                    with Horizontal(classes="field-row"):
                        with Vertical(classes="field-half"):
                            yield Label("Optimize level 1-3")
                            yield Input(placeholder="3", id="optimize-level")
                        with Vertical(classes="field-half"):
                            yield Label("Lossy 0-200")
                            yield Input(placeholder="0", id="lossy")

                with Vertical(id="group-options", classes="field-group"):
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
        self.app.sub_title = "Compress"
        self.query_one("#group-source", Vertical).border_title = "Source"
        self.query_one("#group-universal", Vertical).border_title = "Quality & sharpen"
        self.query_one("#group-mozjpeg", Vertical).border_title = "mozjpeg options"
        self.query_one("#group-webp", Vertical).border_title = "webp options"
        self.query_one("#group-avif", Vertical).border_title = "avif options"
        self.query_one("#group-oxipng", Vertical).border_title = "oxipng options"
        self.query_one("#group-pngquant", Vertical).border_title = "pngquant options"
        self.query_one("#group-gifsicle", Vertical).border_title = "gifsicle options"
        self.query_one("#group-options", Vertical).border_title = "Options"
        self.query_one("#group-run", Vertical).border_title = "Progress"

        self._config = load_config()
        preset_select = self.query_one("#preset", Select)
        preset_select.set_options(
            [("(none — set everything manually)", "")]
            + [(name, name) for name in sorted(self._config.compress_presets)]
        )
        self._update_codec_visibility()
        self.query_one("#input-files", Input).focus()

    def _update_codec_visibility(self) -> None:
        codec = self.query_one("#codec", Select).value
        for group_codec in CODECS:
            self.query_one(f"#group-{group_codec}", Vertical).display = codec == group_codec

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "codec":
            self._update_codec_visibility()
        elif event.select.id == "preset":
            self._apply_preset(event.value)

    _FIELD_DEFAULTS: dict[str, tuple[str, object]] = {
        "quality": ("input", ""),
        "sharpen_amount": ("input", ""),
        "sharpen_radius": ("input", ""),
        "sharpen_sigma": ("input", ""),
        "sharpen_threshold": ("input", ""),
        "progressive": ("checkbox", True),
        "subsample": ("select", "4:2:0"),
        "lossless": ("checkbox", False),
        "method": ("input", ""),
        "alpha_quality": ("input", ""),
        "speed": ("input", ""),
        "effort": ("input", ""),
        "interlace": ("checkbox", False),
        "quality_range": ("input", ""),
        "pngquant_speed": ("input", ""),
        "dither": ("checkbox", True),
        "optimize_level": ("input", ""),
        "lossy": ("input", ""),
    }

    def _field_id(self, key: str) -> str:
        return key.replace("_", "-")

    def _set_field(self, key: str, kind: str, value: object) -> None:
        field_id = f"#{self._field_id(key)}"
        if kind == "checkbox":
            self.query_one(field_id, Checkbox).value = bool(value)
        elif kind == "select":
            self.query_one(field_id, Select).value = value
        else:
            self.query_one(field_id, Input).value = "" if value == "" else str(value)

    def _apply_preset(self, name: str | None) -> None:
        if not name:
            return
        settings = self._config.compress_presets.get(name)
        if settings is None:
            return

        for key, (kind, default) in self._FIELD_DEFAULTS.items():
            self._set_field(key, kind, default)

        codec = settings.get("codec")
        if codec is not None:
            self.query_one("#codec", Select).value = codec

        for key, value in settings.items():
            if key == "codec":
                continue
            kind, _default = self._FIELD_DEFAULTS.get(key, ("input", ""))
            self._set_field(key, kind, value)

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

    def _parse_optional_float(self, field_id: str, label: str) -> tuple[float | None, bool]:
        raw = self.query_one(field_id, Input).value.strip()
        if not raw:
            return None, True
        try:
            return float(raw), True
        except ValueError:
            self._flash_error(field_id, f"{label} must be a number: {raw!r}")
            return None, False

    def _resolve_form(self) -> tuple[list[Path], str, dict[str, Any], bool] | None:
        error_fields = [
            "#input-files", "#quality", "#sharpen-amount", "#sharpen-radius",
            "#sharpen-sigma", "#sharpen-threshold", "#method", "#alpha-quality",
            "#speed", "#effort", "#pngquant-speed", "#optimize-level", "#lossy",
        ]
        for field_id in error_fields:
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

        codec = self.query_one("#codec", Select).value
        if codec in (None, Select.BLANK, Select.NULL):
            codec = "mozjpeg"

        try:
            check_no_output_collisions(files, codec)
        except InvalidCompressInputError as exc:
            self._flash_error("#input-files", str(exc))
            return None

        quality_str = self.query_one("#quality", Input).value.strip()
        try:
            quality = int(quality_str) if quality_str else None
            if quality is not None and not (0 <= quality <= 100):
                raise ValueError
        except ValueError:
            self._flash_error("#quality", f"quality must be 0-100: {quality_str!r}")
            return None

        sharpen_amount, ok = self._parse_optional_float("#sharpen-amount", "sharpen amount")
        if not ok:
            return None
        sharpen_radius, ok = self._parse_optional_float("#sharpen-radius", "sharpen radius")
        if not ok:
            return None
        sharpen_sigma, ok = self._parse_optional_float("#sharpen-sigma", "sharpen sigma")
        if not ok:
            return None
        sharpen_threshold, ok = self._parse_optional_float("#sharpen-threshold", "sharpen threshold")
        if not ok:
            return None

        overwrite = self.query_one("#overwrite", Checkbox).value

        kwargs: dict[str, Any] = {
            "quality": quality,
            "sharpen_amount": sharpen_amount,
            "sharpen_radius": sharpen_radius if sharpen_radius is not None else 2.0,
            "sharpen_sigma": sharpen_sigma if sharpen_sigma is not None else 1.0,
            "sharpen_threshold": sharpen_threshold if sharpen_threshold is not None else 0.0,
        }

        if codec == "mozjpeg":
            kwargs["progressive"] = self.query_one("#progressive", Checkbox).value
            kwargs["subsample"] = self.query_one("#subsample", Select).value
        elif codec == "webp":
            kwargs["lossless"] = self.query_one("#lossless", Checkbox).value
            method_str = self.query_one("#method", Input).value.strip()
            try:
                kwargs["method"] = int(method_str) if method_str else 6
            except ValueError:
                self._flash_error("#method", f"method must be 0-6: {method_str!r}")
                return None
            alpha_str = self.query_one("#alpha-quality", Input).value.strip()
            try:
                kwargs["alpha_quality"] = int(alpha_str) if alpha_str else 100
            except ValueError:
                self._flash_error("#alpha-quality", f"alpha quality must be 0-100: {alpha_str!r}")
                return None
        elif codec == "avif":
            speed_str = self.query_one("#speed", Input).value.strip()
            try:
                kwargs["speed"] = int(speed_str) if speed_str else 6
            except ValueError:
                self._flash_error("#speed", f"speed must be 0-10: {speed_str!r}")
                return None
        elif codec == "oxipng":
            effort_str = self.query_one("#effort", Input).value.strip()
            try:
                kwargs["effort"] = int(effort_str) if effort_str else 4
            except ValueError:
                self._flash_error("#effort", f"effort must be 0-6: {effort_str!r}")
                return None
            kwargs["interlace"] = self.query_one("#interlace", Checkbox).value
        elif codec == "pngquant":
            kwargs["quality_range"] = self.query_one("#quality-range", Input).value.strip() or "65-90"
            speed_str = self.query_one("#pngquant-speed", Input).value.strip()
            try:
                kwargs["pngquant_speed"] = int(speed_str) if speed_str else 4
            except ValueError:
                self._flash_error("#pngquant-speed", f"speed must be 1-11: {speed_str!r}")
                return None
            kwargs["dither"] = self.query_one("#dither", Checkbox).value
        else:  # gifsicle
            level_str = self.query_one("#optimize-level", Input).value.strip()
            try:
                kwargs["optimize_level"] = int(level_str) if level_str else 3
            except ValueError:
                self._flash_error("#optimize-level", f"optimize level must be 1-3: {level_str!r}")
                return None
            lossy_str = self.query_one("#lossy", Input).value.strip()
            try:
                kwargs["lossy"] = int(lossy_str) if lossy_str else 0
            except ValueError:
                self._flash_error("#lossy", f"lossy must be 0-200: {lossy_str!r}")
                return None

        return files, codec, kwargs, overwrite

    def action_run(self) -> None:
        resolved = self._resolve_form()
        if resolved is None:
            return
        files, codec, kwargs, overwrite = resolved

        assert self._config is not None
        self.query_one("#run-btn", Button).disabled = True
        self.query_one("#run-progress", ProgressBar).update(total=len(files), progress=0)
        self.query_one("#run-summary", Static).update("")

        self._run_compress_worker(files, codec, kwargs, overwrite)

    @work(thread=True, exclusive=True, group="compress-run")
    def _run_compress_worker(
        self, files: list[Path], codec: str, kwargs: dict[str, Any], overwrite: bool
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
                outcome = compress_file(
                    input_path, codec, tools=self._config.tools, overwrite=overwrite, **kwargs
                )
            except (CompressError, MysuiteToolError) as exc:
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
        self, outcome: CompressOutcome | None, error: str | None, input_path: Path
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
