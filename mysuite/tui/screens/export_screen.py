from __future__ import annotations

from pathlib import Path

from textual import events, work
from textual.app import ComposeResult
from textual.containers import Horizontal, HorizontalScroll, Vertical, VerticalScroll
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
    SelectionList,
    Static,
)
from textual.worker import get_current_worker

from mysuite.config import (
    DEFAULT_NAMING_TEMPLATE,
    DEFAULT_PATH_TEMPLATE,
    Config,
    ExportSettings,
    load_config,
)
from mysuite.config_write import save_preset
from mysuite.export._parsing import InvalidInputError, parse_sizes, resolve_input_files
from mysuite.export.models import ExportJob, ExportPlan
from mysuite.export.naming import resolve_naming_templates, resolve_path_template_for_variant
from mysuite.export.planner import MysuitePlannerError, build_plan
from mysuite.export.recolor import InvalidRecolorError, apply_recolor, parse_recolor_pairs
from mysuite.export.renderer import ExecutionResult, Renderer
from mysuite.export.units import VALID_UNITS, InvalidPaddingError, parse_padding, parse_size
from mysuite.tui.dragdrop import merge_paths_into_input, parse_dropped_paths, parse_input_files_field
from mysuite.tui.widgets.recolor_row import RecolorRow
from mysuite.tui.widgets.svg_preview import SvgThumbnail, render_svg_thumbnail
from mysuite.utils.subprocess_utils import MysuiteToolError

FORMAT_OPTIONS = ["png", "pdf", "eps", "svg", "jpeg", "webp", "tiff", "ico", "icns"]
PROFILE_OPTIONS = ["rgb", "cmyk"]
UNIT_OPTIONS = ["px", "mm", "cm", "in"]
assert set(UNIT_OPTIONS) == VALID_UNITS


class SvgDropInput(Input):
    """An Input that also accepts a dragged-in file: most terminals turn an OS
    file-drop into a Paste of the file's path. Textual calls a _on_paste
    defined on EVERY class in the MRO for the same event (not just the
    most-derived one, unlike normal Python method overriding) — so on top of
    this override, Input's own default single-line-insert _on_paste would
    ALSO still run afterwards and duplicate the text unless explicitly
    suppressed via event.prevent_default() (event.stop() alone only stops
    bubbling to ancestor widgets, not sibling handlers up the MRO). Only when
    the pasted text actually resolves to real SVG file(s)/folder(s) is it
    merged into the field's comma-separated list instead of falling through to
    Input's normal insert-raw-pasted-text behavior."""

    def _on_paste(self, event: events.Paste) -> None:
        dropped = parse_dropped_paths(event.text)
        if dropped:
            self.value = merge_paths_into_input(self.value, dropped)
            event.stop()
            event.prevent_default()


class ExportScreen(Screen):
    BINDINGS = [
        ("escape", "go_back", "Back"),
        ("ctrl+r", "run", "Run"),
        ("ctrl+s", "save_preset", "Save preset"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._config: Config | None = None
        self._settings: ExportSettings | None = None
        self._last_plans: list[tuple[Path, ExportPlan]] = []
        self._last_dpi: float = 300.0
        self._last_background: str | None = None
        self._last_quality: int | None = None
        self._last_margin_top: str | None = None
        self._last_margin_right: str | None = None
        self._last_margin_bottom: str | None = None
        self._last_margin_left: str | None = None
        self._last_png_compression: int | None = None
        self._last_name_override: str | None = None
        self._last_recolor_map: dict[str, str] = {}
        self.last_summary_text: str = ""
        self._preview_debounce_timer: Timer | None = None
        self._preview_thumb_paths: list[Path] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with Vertical(id="form-pane"):
                with Horizontal(id="form-columns"):
                    with VerticalScroll(id="form-col-left", classes="form-col"):
                        with Vertical(id="group-source", classes="field-group"):
                            yield Label(
                                "SVG file(s), a folder, or comma-separated — "
                                "click here, then drag a file in to add it"
                            )
                            with Horizontal(classes="field-row"):
                                yield SvgDropInput(placeholder="path/to/logo.svg or a folder", id="input-svg")
                                yield Button("Browse", id="browse-input-svg")

                            yield Label("Name override (single input only) — blank uses each file's own name")
                            yield Input(placeholder="", id="name-override")

                            yield Label("Sizes — bare numbers use Unit, or add mm/cm/in per size")
                            with Horizontal(classes="field-row"):
                                yield Input(placeholder="16,32,64,128,256,512,1024", id="sizes")
                                yield Select[str](
                                    [(u, u) for u in UNIT_OPTIONS], id="unit", allow_blank=False, value="px"
                                )

                            yield Label("DPI / PPI")
                            yield Input(placeholder="300", id="dpi")

                        with Vertical(id="group-adjust", classes="field-group"):
                            yield Label("Background — e.g. white, #ffffff (blank = transparent)")
                            yield Input(placeholder="", id="background")

                            with Horizontal(classes="field-row"):
                                with Vertical(classes="field-half"):
                                    yield Label("Quality 0-100")
                                    yield Input(placeholder="", id="quality")
                                with Vertical(classes="field-half"):
                                    yield Label("PNG compression 0-9")
                                    yield Input(placeholder="", id="png-compression")

                            yield Label("Margin (all sides unless overridden below) — e.g. 20px or 10%, raster only")
                            yield Input(placeholder="", id="padding")

                            with Horizontal(classes="field-row"):
                                with Vertical(classes="field-half"):
                                    yield Label("Margin top")
                                    yield Input(placeholder="", id="margin-top")
                                with Vertical(classes="field-half"):
                                    yield Label("Margin right")
                                    yield Input(placeholder="", id="margin-right")
                            with Horizontal(classes="field-row"):
                                with Vertical(classes="field-half"):
                                    yield Label("Margin bottom")
                                    yield Input(placeholder="", id="margin-bottom")
                                with Vertical(classes="field-half"):
                                    yield Label("Margin left")
                                    yield Input(placeholder="", id="margin-left")

                            yield Label("Recolor — FROM/TO color swaps (hex or CSS name), add as many as you need")
                            yield Vertical(id="recolor-rows")
                            yield Button("+ Add color swap", id="recolor-add-btn")

                    with VerticalScroll(id="form-col-right", classes="form-col"):
                        with Vertical(id="group-format", classes="field-group"):
                            yield Label("Formats")
                            yield SelectionList[str](
                                *[(fmt, fmt) for fmt in FORMAT_OPTIONS], id="formats"
                            )

                            yield Label("Profiles")
                            yield SelectionList[str](
                                *[(profile, profile) for profile in PROFILE_OPTIONS], id="profiles"
                            )

                        with Vertical(id="group-output", classes="field-group"):
                            yield Label("Output directory")
                            with Horizontal(classes="field-row"):
                                yield Input(placeholder="exports", id="out-dir")
                                yield Button("Browse", id="browse-out-dir")

                            yield Label("Variant — e.g. negative, mono (nests under the primary name folder)")
                            yield Input(placeholder="", id="variant")

                            yield Label("Preset")
                            yield Select[str]([], id="preset", allow_blank=True)

                        with Vertical(id="group-options", classes="field-group"):
                            yield Checkbox("Dry run", id="dry-run")
                            yield Checkbox("Strict (error instead of skip)", id="strict")
                            yield Checkbox("Overwrite existing files", id="overwrite")
                            yield Checkbox("Normalize PNG (sRGB)", value=True, id="normalize-png")
                            yield Checkbox("Add date stamp to filenames (YYYYMMDD)", id="date-stamp")

                with Horizontal(classes="field-row", id="action-row"):
                    yield Button("Run  [ctrl+r]", id="run-btn", variant="primary")
                    yield Button("Save preset  [ctrl+s]", id="save-preset-btn")

            with Vertical(id="results-pane"):
                with Vertical(id="group-svg-preview", classes="field-group"):
                    with HorizontalScroll(id="svg-thumb-strip"):
                        yield Static("(no SVG yet)", id="svg-thumb-placeholder")
                with Vertical(id="group-run", classes="field-group"):
                    yield ProgressBar(id="run-progress", total=100)
                    yield RichLog(id="run-log", markup=True, wrap=True)
                    yield Static("", id="run-summary")
        yield Footer()

    def on_mount(self) -> None:
        self.app.sub_title = "Export"
        self.query_one("#group-source", Vertical).border_title = "Source"
        self.query_one("#group-output", Vertical).border_title = "Output"
        self.query_one("#group-format", Vertical).border_title = "Formats & color"
        self.query_one("#group-adjust", Vertical).border_title = "Adjustments"
        self.query_one("#group-options", Vertical).border_title = "Options"
        self.query_one("#group-svg-preview", Vertical).border_title = "SVG preview"
        self.query_one("#group-run", Vertical).border_title = "Progress"

        self._config = load_config()
        self._settings = self._config.export
        select = self.query_one("#preset", Select)
        select.set_options([(name, name) for name in sorted(self._config.presets)])

        self.query_one("#sizes", Input).value = ",".join(str(s) for s in self._settings.sizes)
        if self._settings.default_unit in UNIT_OPTIONS:
            self.query_one("#unit", Select).value = self._settings.default_unit
        self.query_one("#dpi", Input).value = str(self._settings.dpi)
        self.query_one("#out-dir", Input).value = self._settings.out_dir
        self.query_one("#background", Input).value = self._settings.background or ""
        self.query_one("#quality", Input).value = (
            str(self._settings.quality) if self._settings.quality is not None else ""
        )
        self.query_one("#padding", Input).value = self._settings.padding or ""
        self.query_one("#margin-top", Input).value = self._settings.margin_top or ""
        self.query_one("#margin-right", Input).value = self._settings.margin_right or ""
        self.query_one("#margin-bottom", Input).value = self._settings.margin_bottom or ""
        self.query_one("#margin-left", Input).value = self._settings.margin_left or ""
        self._populate_recolor_rows(self._settings.recolor)
        self.query_one("#variant", Input).value = self._settings.variant or ""
        self.query_one("#png-compression", Input).value = (
            str(self._settings.png_compression) if self._settings.png_compression is not None else ""
        )

        formats_list = self.query_one("#formats", SelectionList)
        for fmt in self._settings.formats:
            if fmt in FORMAT_OPTIONS:
                formats_list.select(fmt)

        profiles_list = self.query_one("#profiles", SelectionList)
        for profile in self._settings.profiles:
            if profile in PROFILE_OPTIONS:
                profiles_list.select(profile)

        self.query_one("#strict", Checkbox).value = self._settings.strict
        self.query_one("#overwrite", Checkbox).value = self._settings.overwrite
        self.query_one("#normalize-png", Checkbox).value = self._settings.normalize_png
        self.query_one("#date-stamp", Checkbox).value = self._settings.date_stamp

        self.query_one("#input-svg", Input).focus()

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "browse-input-svg":
            self._browse_input_svg()
        elif event.button.id == "browse-out-dir":
            self._browse_out_dir()
        elif event.button.id == "run-btn":
            self.action_run()
        elif event.button.id == "save-preset-btn":
            self.action_save_preset()
        elif event.button.id == "recolor-add-btn":
            self._add_recolor_row()

    _PREVIEW_MAX_FILES = 6
    _PREVIEW_DEBOUNCE_SECONDS = 0.4

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "input-svg":
            return
        if self._preview_debounce_timer is not None:
            self._preview_debounce_timer.stop()
        self._preview_debounce_timer = self.set_timer(
            self._PREVIEW_DEBOUNCE_SECONDS, self._trigger_preview_refresh
        )

    def _trigger_preview_refresh(self) -> None:
        self._preview_debounce_timer = None
        if self._config is None:
            return
        value = self.query_one("#input-svg", Input).value.strip()
        if not value:
            self._clear_thumbnail_strip()
            return
        raw_inputs = parse_input_files_field(value)
        try:
            files = resolve_input_files(raw_inputs)
        except InvalidInputError:
            self._clear_thumbnail_strip()
            return
        self._render_svg_previews_worker(files)

    def _clear_thumbnail_strip(self) -> None:
        strip = self.query_one("#svg-thumb-strip", HorizontalScroll)
        strip.remove_children()
        strip.mount(Static("(no SVG yet)", id="svg-thumb-placeholder"))

    @work(thread=True, exclusive=True, group="svg-preview")
    def _render_svg_previews_worker(self, files: list[Path]) -> None:
        assert self._config is not None
        tools = self._config.tools
        shown = files[: self._PREVIEW_MAX_FILES]
        extra = len(files) - len(shown)
        rendered: list[tuple[str, Path]] = []
        for f in shown:
            try:
                png_path = render_svg_thumbnail(f, tools)
            except (MysuiteToolError, OSError):
                continue
            rendered.append((f.stem, png_path))
        self.app.call_from_thread(self._update_thumbnail_strip_ui, rendered, extra)

    def _update_thumbnail_strip_ui(self, rendered: list[tuple[str, Path]], extra: int) -> None:
        strip = self.query_one("#svg-thumb-strip", HorizontalScroll)
        old_paths = self._preview_thumb_paths
        strip.remove_children()
        if not rendered:
            strip.mount(Static("(no SVG yet)", id="svg-thumb-placeholder"))
        else:
            for name, png_path in rendered:
                strip.mount(SvgThumbnail(png_path, name))
            if extra > 0:
                strip.mount(Static(f"+{extra} more"))
        for old_path in old_paths:
            old_path.unlink(missing_ok=True)
        self._preview_thumb_paths = [png_path for _, png_path in rendered]

    def on_unmount(self) -> None:
        if self._preview_debounce_timer is not None:
            self._preview_debounce_timer.stop()
        for path in self._preview_thumb_paths:
            path.unlink(missing_ok=True)
        self._preview_thumb_paths = []

    def _browse_input_svg(self) -> None:
        from mysuite.tui.screens.file_picker import FilePickerScreen

        def apply(path: Path | None) -> None:
            if path is not None:
                self.query_one("#input-svg", Input).value = str(path)

        self.app.push_screen(
            FilePickerScreen(title="Choose input SVG"), apply
        )

    def _browse_out_dir(self) -> None:
        from mysuite.tui.screens.file_picker import FilePickerScreen

        def apply(path: Path | None) -> None:
            if path is not None:
                self.query_one("#out-dir", Input).value = str(path)

        self.app.push_screen(
            FilePickerScreen(
                pick_directories=True, title="Choose output directory"
            ),
            apply,
        )

    def _log(self, message: str) -> None:
        self.query_one("#run-log", RichLog).write(message)

    def _flash_error_widget(self, field: Input, message: str) -> None:
        field.add_class("field-error")
        field.focus()
        self._log(f"[#F87171]✗[/#F87171] {message}")

    def _flash_error(self, field_id: str, message: str) -> None:
        self._flash_error_widget(self.query_one(field_id, Input), message)

    def _populate_recolor_rows(self, recolor: dict[str, str]) -> None:
        rows_container = self.query_one("#recolor-rows", Vertical)
        if not recolor:
            rows_container.mount(RecolorRow())
            return
        for from_val, to_val in recolor.items():
            rows_container.mount(RecolorRow(from_value=from_val, to_value=to_val))

    def _add_recolor_row(self) -> None:
        self.query_one("#recolor-rows", Vertical).mount(RecolorRow())

    def _read_recolor_rows(self) -> dict[str, str] | None:
        mapping: dict[str, str] = {}
        for row in self.query(RecolorRow):
            row.from_input.remove_class("field-error")
            row.to_input.remove_class("field-error")
            from_val = row.from_input.value.strip()
            to_val = row.to_input.value.strip()
            if not from_val and not to_val:
                continue
            if not from_val or not to_val:
                self._flash_error_widget(
                    row.to_input if from_val else row.from_input,
                    "both FROM and TO are required for a color swap row",
                )
                return None
            try:
                mapping.update(parse_recolor_pairs([f"{from_val}={to_val}"]))
            except InvalidRecolorError as exc:
                self._flash_error_widget(row.from_input, str(exc))
                return None
        return mapping

    def _resolve_settings(self) -> tuple[Config, ExportSettings]:
        assert self._config is not None
        preset = self.query_one("#preset", Select).value
        preset_name = None if preset in (None, Select.BLANK, Select.NULL) else preset
        settings = self._config.resolve_export_settings(preset_name)
        return self._config, settings

    def _build_plans_from_form(self) -> list[tuple[Path, ExportPlan]] | None:
        for field_id in (
            "#input-svg", "#name-override", "#sizes", "#dpi", "#quality", "#padding",
            "#margin-top", "#margin-right", "#margin-bottom", "#margin-left", "#png-compression",
            "#variant",
        ):
            self.query_one(field_id, Input).remove_class("field-error")

        config, settings = self._resolve_settings()

        input_svg_str = self.query_one("#input-svg", Input).value.strip()
        if not input_svg_str:
            self._flash_error("#input-svg", "enter an input SVG path, folder, or comma-separated list")
            return None
        raw_inputs = [Path(p.strip()) for p in input_svg_str.split(",") if p.strip()]
        try:
            input_files = resolve_input_files(raw_inputs)
        except InvalidInputError as exc:
            self._flash_error("#input-svg", str(exc))
            return None

        name_override = self.query_one("#name-override", Input).value.strip() or None
        if name_override is not None and len(input_files) > 1:
            self._flash_error("#name-override", "name override only works with a single input file")
            return None

        dpi_str = self.query_one("#dpi", Input).value.strip()
        try:
            dpi = float(dpi_str) if dpi_str else settings.dpi
        except ValueError:
            self._flash_error("#dpi", f"invalid dpi: {dpi_str!r}")
            return None

        unit = self.query_one("#unit", Select).value
        if unit in (None, Select.BLANK, Select.NULL):
            unit = settings.default_unit

        try:
            sizes = parse_sizes(self.query_one("#sizes", Input).value, dpi=dpi, default_unit=unit) or [
                parse_size(s, dpi=dpi, default_unit=unit) for s in settings.sizes
            ]
        except ValueError as exc:
            self._flash_error("#sizes", str(exc))
            return None

        background_str = self.query_one("#background", Input).value.strip()
        background = background_str or settings.background

        quality_str = self.query_one("#quality", Input).value.strip()
        try:
            quality = int(quality_str) if quality_str else settings.quality
            if quality is not None and not (0 <= quality <= 100):
                raise ValueError
        except ValueError:
            self._flash_error("#quality", f"quality must be 0-100: {quality_str!r}")
            return None

        padding_str = self.query_one("#padding", Input).value.strip()
        padding = padding_str or settings.padding

        def resolve_margin_side(field_id: str, settings_value: str | None) -> str | None:
            field_str = self.query_one(field_id, Input).value.strip()
            if field_str:
                return field_str
            if settings_value is not None:
                return settings_value
            return padding

        margin_top = resolve_margin_side("#margin-top", settings.margin_top)
        margin_right = resolve_margin_side("#margin-right", settings.margin_right)
        margin_bottom = resolve_margin_side("#margin-bottom", settings.margin_bottom)
        margin_left = resolve_margin_side("#margin-left", settings.margin_left)

        for field_id, side_value in [
            ("#margin-top", margin_top),
            ("#margin-right", margin_right),
            ("#margin-bottom", margin_bottom),
            ("#margin-left", margin_left),
        ]:
            if side_value:
                try:
                    parse_padding(side_value, reference_px=100)  # syntax check only
                except InvalidPaddingError as exc:
                    self._flash_error(field_id, str(exc))
                    return None

        png_compression_str = self.query_one("#png-compression", Input).value.strip()
        try:
            png_compression = int(png_compression_str) if png_compression_str else settings.png_compression
            if png_compression is not None and not (0 <= png_compression <= 9):
                raise ValueError
        except ValueError:
            self._flash_error("#png-compression", f"png compression must be 0-9: {png_compression_str!r}")
            return None

        formats = list(self.query_one("#formats", SelectionList).selected) or settings.formats
        profiles = list(self.query_one("#profiles", SelectionList).selected) or settings.profiles
        out_dir = Path(self.query_one("#out-dir", Input).value.strip() or settings.out_dir)
        strict = self.query_one("#strict", Checkbox).value

        self._config = config
        self._settings = settings
        self._last_dpi = dpi
        self._last_background = background
        self._last_quality = quality
        self._last_margin_top = margin_top
        self._last_margin_right = margin_right
        self._last_margin_bottom = margin_bottom
        self._last_margin_left = margin_left
        self._last_png_compression = png_compression
        self._last_name_override = name_override

        date_stamp = self.query_one("#date-stamp", Checkbox).value
        naming_template, bundle_naming_template = resolve_naming_templates(
            settings.naming_template,
            settings.bundle_naming_template,
            date_stamp=date_stamp,
            default_naming_template=DEFAULT_NAMING_TEMPLATE,
        )

        variant = self.query_one("#variant", Input).value.strip() or settings.variant
        path_template = resolve_path_template_for_variant(
            settings.path_template, variant=variant, default_path_template=DEFAULT_PATH_TEMPLATE
        )

        row_map = self._read_recolor_rows()
        if row_map is None:
            return None
        recolor_map = row_map if row_map else dict(settings.recolor)
        self._last_recolor_map = recolor_map

        plans: list[tuple[Path, ExportPlan]] = []
        for input_svg in input_files:
            try:
                plan = build_plan(
                    name=name_override or input_svg.stem,
                    out_dir=out_dir,
                    sizes=sizes,
                    formats=formats,
                    profiles=profiles,
                    naming_template=naming_template,
                    path_template=path_template,
                    bundle_naming_template=bundle_naming_template,
                    variant=variant or "",
                    strict=strict,
                )
            except MysuitePlannerError as exc:
                self._log(f"[#F87171]✗[/#F87171] {input_svg}: {exc}")
                return None
            plans.append((input_svg, plan))

        return plans

    def action_run(self) -> None:
        plans = self._build_plans_from_form()
        self._last_plans = plans or []
        if plans is None:
            return
        total = sum(len(p.jobs) + len(p.bundle_jobs) for _, p in plans)
        for _, plan in plans:
            for skip in plan.skips:
                self._log(f"[#FBBF24]⚠ skipping[/#FBBF24] {skip.format}/{skip.colorspace} — {skip.reason}")

        if self.query_one("#dry-run", Checkbox).value:
            self._log(f"[dim]dry run — {total} file(s) would be written, 0 written[/dim]")
            return

        assert self._config is not None
        self.query_one("#run-btn", Button).disabled = True
        self.query_one("#run-progress", ProgressBar).update(total=total, progress=0)
        self.query_one("#run-summary", Static).update("")

        normalize_png = self.query_one("#normalize-png", Checkbox).value
        overwrite = self.query_one("#overwrite", Checkbox).value
        self._run_export_worker(
            plans,
            self._last_dpi,
            normalize_png,
            overwrite,
            self._last_background,
            self._last_quality,
            self._last_margin_top,
            self._last_margin_right,
            self._last_margin_bottom,
            self._last_margin_left,
            self._last_png_compression,
            self._last_recolor_map,
        )

    @work(thread=True, exclusive=True, group="export-run")
    def _run_export_worker(
        self,
        plans: list[tuple[Path, ExportPlan]],
        dpi: float,
        normalize_png: bool,
        overwrite: bool,
        background: str | None,
        quality: int | None,
        margin_top: str | None,
        margin_right: str | None,
        margin_bottom: str | None,
        margin_left: str | None,
        png_compression: int | None,
        recolor_map: dict[str, str],
    ) -> None:
        worker = get_current_worker()
        assert self._config is not None
        renderer = Renderer(
            self._config.tools,
            dpi=dpi,
            normalize_png=normalize_png,
            overwrite=overwrite,
            background=background,
            quality=quality,
            margin_top=margin_top,
            margin_right=margin_right,
            margin_bottom=margin_bottom,
            margin_left=margin_left,
            png_compression=png_compression,
        )

        def on_job_done(job, skipped: bool) -> None:
            if worker.is_cancelled:
                return
            self.app.call_from_thread(self._on_job_done_ui, job, skipped)

        combined = ExecutionResult()
        for input_svg, plan in plans:
            if worker.is_cancelled:
                break
            render_svg, is_temp = apply_recolor(input_svg, recolor_map)
            try:
                result = renderer.execute(plan, render_svg, on_job_done=on_job_done)
            finally:
                if is_temp:
                    render_svg.unlink(missing_ok=True)
            combined.written.extend(result.written)
            combined.skipped_existing.extend(result.skipped_existing)
        self.app.call_from_thread(self._on_run_complete, combined)

    def _on_job_done_ui(self, job: ExportJob, skipped: bool) -> None:
        self.query_one("#run-progress", ProgressBar).advance(1)
        if skipped:
            self._log(f"[dim]— exists, skipped: {job.output_path}[/dim]")
        else:
            self._log(f"[#4ADE80]✓[/#4ADE80] {job.output_path}")

    def _on_run_complete(self, result: ExecutionResult) -> None:
        self.query_one("#run-btn", Button).disabled = False
        self.last_summary_text = (
            f"done — {len(result.written)} written, "
            f"{len(result.skipped_existing)} already existed (enable Overwrite to replace)"
        )
        self.query_one("#run-summary", Static).update(
            f"[bold green]done[/bold green] — {len(result.written)} written, "
            f"{len(result.skipped_existing)} already existed (enable Overwrite to replace)"
        )

    def action_save_preset(self) -> None:
        from mysuite.tui.screens.save_preset_modal import SavePresetModal

        def apply(name: str | None) -> None:
            if name is None:
                return
            self._save_current_as_preset(name)

        self.app.push_screen(SavePresetModal(), apply)

    def _save_current_as_preset(self, name: str) -> None:
        formats = list(self.query_one("#formats", SelectionList).selected)
        profiles = list(self.query_one("#profiles", SelectionList).selected)
        settings: dict = {}
        sizes_str = self.query_one("#sizes", Input).value.strip()
        if sizes_str:
            settings["sizes"] = [s.strip() for s in sizes_str.split(",") if s.strip()]
        if formats:
            settings["formats"] = formats
        if profiles:
            settings["profiles"] = profiles
        dpi_str = self.query_one("#dpi", Input).value.strip()
        if dpi_str:
            settings["dpi"] = float(dpi_str)
        unit_value = self.query_one("#unit", Select).value
        if unit_value not in (None, Select.BLANK, Select.NULL):
            settings["default_unit"] = unit_value
        out_dir_str = self.query_one("#out-dir", Input).value.strip()
        if out_dir_str:
            settings["out_dir"] = out_dir_str
        background_str = self.query_one("#background", Input).value.strip()
        if background_str:
            settings["background"] = background_str
        quality_str = self.query_one("#quality", Input).value.strip()
        if quality_str:
            settings["quality"] = int(quality_str)
        padding_str = self.query_one("#padding", Input).value.strip()
        if padding_str:
            settings["padding"] = padding_str
        margin_top_str = self.query_one("#margin-top", Input).value.strip()
        if margin_top_str:
            settings["margin_top"] = margin_top_str
        margin_right_str = self.query_one("#margin-right", Input).value.strip()
        if margin_right_str:
            settings["margin_right"] = margin_right_str
        margin_bottom_str = self.query_one("#margin-bottom", Input).value.strip()
        if margin_bottom_str:
            settings["margin_bottom"] = margin_bottom_str
        margin_left_str = self.query_one("#margin-left", Input).value.strip()
        if margin_left_str:
            settings["margin_left"] = margin_left_str
        row_map = self._read_recolor_rows()
        if row_map is None:
            return
        if row_map:
            settings["recolor"] = row_map
        variant_str = self.query_one("#variant", Input).value.strip()
        if variant_str:
            settings["variant"] = variant_str
        png_compression_str = self.query_one("#png-compression", Input).value.strip()
        if png_compression_str:
            settings["png_compression"] = int(png_compression_str)
        settings["strict"] = self.query_one("#strict", Checkbox).value
        settings["overwrite"] = self.query_one("#overwrite", Checkbox).value
        settings["normalize_png"] = self.query_one("#normalize-png", Checkbox).value
        settings["date_stamp"] = self.query_one("#date-stamp", Checkbox).value

        target = Path.cwd() / "mysuite.toml"
        save_preset(target, name, settings)
        self._log(f"[bold green]saved[/bold green] preset {name!r} to {target}")

        select = self.query_one("#preset", Select)
        assert self._config is not None
        self._config = load_config()
        select.set_options([(n, n) for n in sorted(self._config.presets)])
        select.value = name
