"""Export: SVG logos -> PNG/PDF/EPS/SVG/JPEG/WebP/TIFF/ICO/ICNS in any sizes, RGB or CMYK, with colour variants.

The form is a front end for `mysuite export …`; see mysuite.tui.shell. Field ids are stable on purpose
(`#input-svg`, `#sizes`, `#formats`, …) — tests and agents drive the screen through them.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Checkbox, Collapsible, Input, Label, Select, Static

from mysuite import profiles as profiles_mod
from mysuite.color import variants as variant_mod
from mysuite.config import Config, load_config
from mysuite.config_write import save_preset
from mysuite.export._parsing import VALID_FORMATS
from mysuite.export.units import VALID_UNITS
from mysuite.tui.shell import ChipGroup, FormError, ToolScreen, field
from mysuite.tui.widgets.file_target import FileTarget
from mysuite.tui.widgets.recolor_row import RecolorRow
from mysuite.utils.paths import show_path

FORMAT_OPTIONS = ["png", "pdf", "eps", "svg", "jpeg", "webp", "tiff", "ico", "icns"]
PROFILE_OPTIONS = ["rgb", "cmyk"]
UNIT_OPTIONS = ["px", "mm", "cm", "in"]
CMYK_MODES = ["exact", "clean", "clean:10", "tokens"]
assert set(UNIT_OPTIONS) == VALID_UNITS
assert set(FORMAT_OPTIONS) == set(VALID_FORMATS)

_SVG = frozenset({".svg"})
_BLANK = (None, Select.BLANK, Select.NULL)


def _select_value(widget: Select) -> str | None:
    return None if widget.value in _BLANK else str(widget.value)


class ExportScreen(ToolScreen):
    TOOL_KEY = "export"
    CLI = ("export",)
    HEADING = "Export"

    PERSIST = (
        "sizes", "unit", "dpi", "formats", "profiles", "cmyk-mode", "variants", "out-dir", "background",
        "quality", "png-compression", "padding", "margin-top", "margin-right", "margin-bottom", "margin-left",
        "variant", "tokens", "brand", "theme", "overwrite", "strict", "normalize-png", "date-stamp", "recursive",
    )

    BINDINGS = [("escape", "go_back", "Back"), ("ctrl+r", "run", "Run"), ("ctrl+s", "save_preset", "Save preset")]

    def __init__(self) -> None:
        super().__init__()
        self._config: Config | None = None
        self._default_formats: list[str] = ["png"]
        self._default_profiles: list[str] = ["rgb"]

    # ---------------------------------------------------------------------------- form
    def compose_form(self) -> ComposeResult:
        yield Label("SVG files", classes="section")
        yield FileTarget(
            input_id="input-svg", browse_id="browse-input-svg", extensions=_SVG, noun="SVG",
            placeholder="logo.svg, a folder, or a glob like brand/*.svg",
        )
        yield Checkbox("Include subfolders", id="recursive")

        yield Label("What to make", classes="section")
        with Horizontal(classes="pair"):
            yield field("Sizes", Input(placeholder="16,32,64,128,256,512,1024", id="sizes"), classes="grow")
            yield field("Unit", Select[str]([(u, u) for u in UNIT_OPTIONS], id="unit", prompt="default"), classes="narrow")
            yield field("DPI", Input(placeholder="300", id="dpi"), classes="narrow")
        yield field("Formats", ChipGroup(FORMAT_OPTIONS, id="formats", columns=5))
        with Horizontal(classes="pair"):
            yield field("Colour", ChipGroup(PROFILE_OPTIONS, id="profiles", columns=2), classes="narrow")
            yield field(
                "CMYK numbers", Select[str]([(m, m) for m in CMYK_MODES], id="cmyk-mode", prompt="exact (default)"),
                classes="grow",
            )
        yield field("Colour variants (none ticked = just the logo as it is)", ChipGroup(list(variant_mod.ALL), id="variants", columns=4))

        yield Label("Where", classes="section")
        with Horizontal(classes="field-row"):
            yield Input(placeholder="exports", id="out-dir")
            yield Button("Browse", id="browse-out-dir", classes="mini")

        yield Label("Preset & profile", classes="section")
        with Horizontal(classes="pair"):
            yield field("Preset", Select[str]([], id="preset", prompt="none"), classes="grow")
            yield field("Company profile", Select[str]([], id="profile", prompt="none"), classes="grow")

        with Collapsible(title="Size, margin & quality", collapsed=True, id="adv-adjust"):
            yield field("Background (blank = transparent)", Input(placeholder="white or #ffffff", id="background"))
            with Horizontal(classes="pair"):
                yield field("Quality 0-100", Input(id="quality"))
                yield field("PNG compression 0-9", Input(id="png-compression"))
            yield field("Margin, all sides (raster only)", Input(placeholder="20px or 10%", id="padding"))
            with Horizontal(classes="pair"):
                yield field("Top", Input(id="margin-top"))
                yield field("Right", Input(id="margin-right"))
                yield field("Bottom", Input(id="margin-bottom"))
                yield field("Left", Input(id="margin-left"))
        with Collapsible(title="Colours: swaps & design tokens", collapsed=True, id="adv-colour"):
            yield Label("Swap colours: FROM → TO (hex, name or token:NAME)", classes="fl")
            yield Vertical(RecolorRow(), id="recolor-rows")
            yield Button("+ Add swap", id="recolor-add-btn", classes="mini")
            yield field("Design tokens source", Input(placeholder="file, folder, git+https://…@ref or figma:KEY", id="tokens"))
            with Horizontal(classes="pair"):
                yield field("Brand", Input(id="brand"))
                yield field("Theme", Select[str]([("light", "light"), ("dark", "dark")], id="theme", prompt="light"))
        with Collapsible(title="Names & safety", collapsed=True, id="adv-names"):
            with Horizontal(classes="pair"):
                yield field("Name (single file only)", Input(id="name"))
                yield field("Variant folder", Input(id="variant"))
            yield Checkbox("Date stamp in file names (YYYYMMDD)", id="date-stamp")
            yield Checkbox("Replace files that already exist", id="overwrite")
            yield Checkbox("Strict: error instead of skipping", id="strict")
            yield Checkbox("Tag PNGs as sRGB", value=True, id="normalize-png")

    def prepare(self) -> None:
        self._config = load_config()
        self.query_one("#preset", Select).set_options([(n, n) for n in sorted(self._config.presets)])
        self.query_one("#profile", Select).set_options([(n, n) for n in sorted(self._config.profiles)])

    def apply_defaults(self) -> None:
        assert self._config is not None
        settings = self._config.export
        self._default_formats = [f for f in settings.formats if f in FORMAT_OPTIONS]
        self._default_profiles = [p for p in settings.profiles if p in PROFILE_OPTIONS]
        self.query_one("#formats", ChipGroup).select_only(self._default_formats)
        self.query_one("#profiles", ChipGroup).select_only(self._default_profiles)
        self.query_one("#sizes", Input).placeholder = ",".join(str(s) for s in settings.sizes)
        self.query_one("#dpi", Input).placeholder = str(settings.dpi)
        self.query_one("#out-dir", Input).placeholder = settings.out_dir
        self.query_one("#normalize-png", Checkbox).value = settings.normalize_png

    def extra_state(self) -> dict[str, Any]:
        return {"recolor": [[r.from_input.value, r.to_input.value] for r in self.query(RecolorRow) if r.from_input.value or r.to_input.value]}

    def restore_extra(self, values: dict[str, Any]) -> None:
        container = self.query_one("#recolor-rows", Vertical)
        pairs = [p for p in values.get("recolor") or [] if isinstance(p, list) and len(p) == 2]
        rows = list(container.query(RecolorRow))
        if not pairs:                                   # keep one empty row; nothing to remount
            for row in rows[1:]:
                row.remove()
            if rows:
                rows[0].from_input.value = rows[0].to_input.value = ""
            return
        for row in rows:
            row.remove()
        container.mount(*[RecolorRow(from_value=str(a), to_value=str(b)) for a, b in pairs])

    # ---------------------------------------------------------------------- command
    def _text(self, wid: str) -> str:
        return self.query_one(f"#{wid}", Input).value.strip()

    def _number(self, wid: str, label: str, *, whole: bool, low: float, high: float | None = None) -> str | None:
        raw = self._text(wid)
        if not raw:
            return None
        try:
            value = int(raw) if whole else float(raw)
        except ValueError:
            raise FormError(f"{label} must be a {'whole ' if whole else ''}number, not {raw!r}", wid) from None
        if value < low or (high is not None and value > high):
            span = f"{low:g}-{high:g}" if high is not None else f"at least {low:g}"
            raise FormError(f"{label} must be {span}", wid)
        return raw

    def global_argv(self) -> list[str]:
        profile = _select_value(self.query_one("#profile", Select))
        return ["--profile", profile] if profile else []

    def argv(self) -> list[str]:
        res = self.query_one(FileTarget).resolve()
        if res.empty:
            raise FormError("choose at least one SVG — paste a path, drop a file, or use Files… / Folder…", "input-svg")
        if res.missing or res.unsupported or res.empty_folders:
            bad = (res.missing or [str(p) for p in res.unsupported] or [str(p) for p in res.empty_folders])[0]
            raise FormError(f"not usable: {bad}" if res.missing or res.unsupported else f"no SVG files in {bad}", "input-svg")
        out: list[str] = []

        def opt(flag: str, value: str | None) -> None:
            if value:
                out.extend([flag, value])

        opt("--sizes", self._text("sizes") or None)
        unit = _select_value(self.query_one("#unit", Select))
        opt("--unit", unit)
        opt("--dpi", self._number("dpi", "DPI", whole=False, low=1))
        formats = self.query_one("#formats", ChipGroup).selected
        if formats and formats != [f for f in FORMAT_OPTIONS if f in self._default_formats]:
            opt("--formats", ",".join(formats))
        profiles = self.query_one("#profiles", ChipGroup).selected
        if profiles and profiles != [p for p in PROFILE_OPTIONS if p in self._default_profiles]:
            opt("--profiles", ",".join(profiles))
        opt("--cmyk-mode", _select_value(self.query_one("#cmyk-mode", Select)))
        variants = self.query_one("#variants", ChipGroup).selected
        if variants:
            opt("--variants", ",".join(variants))
        opt("--out", self._text("out-dir") or None)
        opt("--preset", _select_value(self.query_one("#preset", Select)))
        opt("--background", self._text("background") or None)
        opt("--quality", self._number("quality", "Quality", whole=True, low=0, high=100))
        opt("--png-compression", self._number("png-compression", "PNG compression", whole=True, low=0, high=9))
        opt("--padding", self._text("padding") or None)
        for side in ("top", "right", "bottom", "left"):
            opt(f"--margin-{side}", self._text(f"margin-{side}") or None)
        name = self._text("name")
        if name:
            if len(res.files) != 1:
                raise FormError("a name only works with a single file", "name")
            opt("--name", name)
        opt("--variant", self._text("variant") or None)
        for row in self.query(RecolorRow):
            src, dst = row.from_input.value.strip(), row.to_input.value.strip()
            if src and dst:
                out.extend(["--recolor", f"{src}={dst}"])
            elif src or dst:
                raise FormError("a colour swap needs both FROM and TO", None)
        opt("--tokens", self._text("tokens") or None)
        opt("--brand", self._text("brand") or None)
        opt("--theme", _select_value(self.query_one("#theme", Select)))
        if self.query_one("#recursive", Checkbox).value:
            out.append("--recursive")
        if self.query_one("#date-stamp", Checkbox).value:
            out.append("--date-stamp")
        if self.query_one("#overwrite", Checkbox).value:
            out.append("--overwrite")
        if self.query_one("#strict", Checkbox).value:
            out.append("--strict")
        if not self.query_one("#normalize-png", Checkbox).value:
            out.append("--no-normalize-png")
        if any(a.startswith("-") for a in res.args):     # a file named like an option: everything after -- is a path
            return [*out, "--", *res.args]
        return [*res.args, *out]

    def output_dir(self) -> Path | None:
        folder = self._text("out-dir") or (self._config.export.out_dir if self._config else "exports")
        return Path(folder).expanduser().resolve()

    # ------------------------------------------------------------------------ results
    def plan_text(self, report: dict[str, Any]) -> str:
        items = report.get("items", [])
        if not items:
            return "nothing to do"
        sources = {i.get("input") for i in items}
        formats = Counter(f"{i.get('format')}{'/cmyk' if i.get('colorspace') == 'cmyk' else ''}" for i in items)
        folder = show_path(self.output_dir()) if self.output_dir() else ""
        by_format = ", ".join(f"{name} ×{n}" for name, n in formats.most_common())
        return f"{len(items)} file(s) from {len(sources)} SVG(s) → {folder}\n{by_format}"

    # ------------------------------------------------------------------------- events
    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "browse-out-dir":
            from mysuite.tui.screens.file_picker import FilePickerScreen

            def apply(path: Path | None) -> None:
                if path is not None:
                    self.query_one("#out-dir", Input).value = str(path)

            self.app.push_screen(FilePickerScreen(pick_directories=True, title="Choose output directory"), apply)
        elif bid == "recolor-add-btn":
            self.query_one("#recolor-rows", Vertical).mount(RecolorRow())
        else:
            super().on_button_pressed(event)

    # ------------------------------------------------------------------- save preset
    def action_save_preset(self) -> None:
        from mysuite.tui.screens.save_preset_modal import SavePresetModal

        def apply(name: str | None) -> None:
            if name:
                self._save_current_as_preset(name)

        self.app.push_screen(SavePresetModal(), apply)

    def _save_current_as_preset(self, name: str) -> None:
        settings: dict[str, Any] = {}
        sizes = self._text("sizes")
        if sizes:
            settings["sizes"] = [s.strip() for s in sizes.split(",") if s.strip()]
        for key, wid in (("formats", "formats"), ("profiles", "profiles")):
            chosen = self.query_one(f"#{wid}", ChipGroup).selected
            if chosen:
                settings[key] = chosen
        unit = _select_value(self.query_one("#unit", Select))
        if unit:
            settings["default_unit"] = unit
        cmyk = _select_value(self.query_one("#cmyk-mode", Select))
        if cmyk:
            settings["cmyk_mode"] = cmyk
        try:
            dpi = self._number("dpi", "DPI", whole=False, low=1)
            quality = self._number("quality", "Quality", whole=True, low=0, high=100)
            compression = self._number("png-compression", "PNG compression", whole=True, low=0, high=9)
        except FormError as exc:
            self.flash(exc.widget_id, str(exc))
            return
        if dpi:
            settings["dpi"] = float(dpi)
        if quality:
            settings["quality"] = int(quality)
        if compression:
            settings["png_compression"] = int(compression)
        for key, wid in (
            ("out_dir", "out-dir"), ("background", "background"), ("padding", "padding"), ("variant", "variant"),
            ("margin_top", "margin-top"), ("margin_right", "margin-right"),
            ("margin_bottom", "margin-bottom"), ("margin_left", "margin-left"),
        ):
            if self._text(wid):
                settings[key] = self._text(wid)
        swaps = {r.from_input.value.strip(): r.to_input.value.strip() for r in self.query(RecolorRow)
                 if r.from_input.value.strip() and r.to_input.value.strip()}
        if swaps:
            settings["recolor"] = swaps
        settings["strict"] = self.query_one("#strict", Checkbox).value
        settings["overwrite"] = self.query_one("#overwrite", Checkbox).value
        settings["normalize_png"] = self.query_one("#normalize-png", Checkbox).value
        settings["date_stamp"] = self.query_one("#date-stamp", Checkbox).value

        target = Path.cwd() / "mysuite.toml"
        save_preset(target, name, settings)
        self.write_log(f"[bold green]saved[/bold green] preset {name!r} to {target}")
        self._config = load_config()
        select = self.query_one("#preset", Select)
        select.set_options([(n, n) for n in sorted(self._config.presets)])
        select.value = name
