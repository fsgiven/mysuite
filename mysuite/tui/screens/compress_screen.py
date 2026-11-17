"""Compress / Save for web: shrink images with the right codec (mozjpeg, webp, avif, oxipng, pngquant, gifsicle) — copies,
originals untouched.

A form over `mysuite compress …`; see mysuite.tui.shell. Stable ids: `#input-files`, `#preset`, `#codec`, `#quality`,
the Save-for-web limits `#max-kb`, `#max-size`, `#background`, and the per-codec fields in `#group-<codec>` (only the chosen
codec's group is shown). The command always spells out the values (a preset only fills the form), so what you see is
exactly what runs. Convert's "Save for web…" button opens this screen with its files and the `save-for-web` preset.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Checkbox, Collapsible, Input, Label, Select, Static

from mysuite.compress._parsing import CODECS
from mysuite.compress.compress import MAX_KB_CODECS
from mysuite.config import load_config
from mysuite.convert._parsing import SOURCE_EXTENSIONS
from mysuite.tui.shell import FormError, ToolScreen, field
from mysuite.tui.widgets.file_target import FileTarget

_SOURCES = frozenset(SOURCE_EXTENSIONS)
_BLANK = (None, Select.BLANK, Select.NULL)
_CODEC_LABELS = [
    ("mozjpeg — JPEG", "mozjpeg"),
    ("cwebp — WebP", "webp"),
    ("avifenc — AVIF", "avif"),
    ("oxipng — PNG (lossless)", "oxipng"),
    ("pngquant — PNG (lossy)", "pngquant"),
    ("gifsicle — GIF", "gifsicle"),
]
assert [c for _, c in _CODEC_LABELS] == list(CODECS)

# field id -> (kind, default used when a preset does not mention it)
_FIELD_DEFAULTS: dict[str, tuple[str, object]] = {
    "quality": ("input", ""), "max-kb": ("input", ""), "max-size": ("input", ""), "background": ("input", ""),
    "sharpen-amount": ("input", ""), "sharpen-radius": ("input", ""),
    "sharpen-sigma": ("input", ""), "sharpen-threshold": ("input", ""),
    "progressive": ("checkbox", True), "subsample": ("select", "4:2:0"),
    "lossless": ("checkbox", False), "method": ("input", ""), "alpha-quality": ("input", ""),
    "speed": ("input", ""), "effort": ("input", ""), "interlace": ("checkbox", False),
    "quality-range": ("input", ""), "pngquant-speed": ("input", ""), "dither": ("checkbox", True),
    "optimize-level": ("input", ""), "lossy": ("input", ""),
}
# (flag, field id, label, whole number?, low, high) per codec; quality + sharpen apply to every codec
_NUMBERS: dict[str, list[tuple[str, str, str, bool, float, float | None]]] = {
    "all": [
        ("--quality", "quality", "Quality", True, 0, 100),
        ("--max-size", "max-size", "Longest side", True, 1, None),
        ("--sharpen-amount", "sharpen-amount", "Sharpen amount", False, 0, None),
        ("--sharpen-radius", "sharpen-radius", "Sharpen radius", False, 0, None),
        ("--sharpen-sigma", "sharpen-sigma", "Sharpen sigma", False, 0, None),
        ("--sharpen-threshold", "sharpen-threshold", "Sharpen threshold", False, 0, None),
    ],
    "webp": [("--method", "method", "Method", True, 0, 6), ("--alpha-quality", "alpha-quality", "Alpha quality", True, 0, 100)],
    "avif": [("--speed", "speed", "Speed", True, 0, 10)],
    "oxipng": [("--effort", "effort", "Effort", True, 0, 6)],
    "pngquant": [("--pngquant-speed", "pngquant-speed", "Speed", True, 1, 11)],
    "gifsicle": [("--optimize-level", "optimize-level", "Optimize level", True, 1, 3), ("--lossy", "lossy", "Lossy", True, 0, 200)],
}


def _size(n: int) -> str:
    return f"{n / 1_000_000:.1f} MB" if n >= 1_000_000 else f"{n / 1000:.0f} KB" if n >= 10_000 else f"{n / 1000:.1f} KB"


class CompressScreen(ToolScreen):
    TOOL_KEY = "compress"
    CLI = ("compress",)
    HEADING = "Compress · Save for web"
    PERSIST = ("codec", *_FIELD_DEFAULTS, "overwrite", "recursive")
    WEB_PRESET = "save-for-web"

    def __init__(self, *, files: str | None = None, preset: str | None = None) -> None:
        super().__init__()
        self._initial_files = files
        self._initial_preset = preset

    def compose_form(self) -> ComposeResult:
        yield Label("Images", classes="section")
        yield FileTarget(
            input_id="input-files", browse_id="browse-input-files", extensions=_SOURCES, noun="image",
            placeholder="photo.jpg, a folder, or a glob like ~/Pictures/*.png",
        )
        yield Checkbox("Include subfolders", id="recursive")

        yield Label("How", classes="section")
        with Horizontal(classes="pair"):
            yield field("Preset (fills the form)", Select[str]([("(none — set everything manually)", "")], id="preset", allow_blank=False, value=""), classes="grow")
            yield field("Codec", Select[str](_CODEC_LABELS, id="codec", allow_blank=False, value="mozjpeg"), classes="grow")
        with Horizontal(classes="pair"):
            yield field("Quality 0-100 (the highest, with a size limit)", Input(placeholder="codec default", id="quality"))
            yield field("Sharpen amount", Input(placeholder="off", id="sharpen-amount"))

        yield Label("Save for web", classes="section")
        with Horizontal(classes="pair"):
            yield field("Max file size, KB (JPEG, WebP, AVIF)", Input(placeholder="no limit", id="max-kb"))
            yield field("Max longest side, px", Input(placeholder="keep size", id="max-size"))
        yield field("Background for transparency", Input(placeholder="JPEG: white · others keep it", id="background"))
        yield Static("Always: upright, sRGB embedded, GPS and serial numbers removed, copyright kept; "
                     "never bigger than the original.", classes="hint")

        with Vertical(id="group-mozjpeg"):
            with Horizontal(classes="pair"):
                yield field("Chroma subsampling", Select[str]([("4:2:0 (smaller)", "4:2:0"), ("4:4:4 (sharper colour)", "4:4:4")], id="subsample", allow_blank=False, value="4:2:0"))
            yield Checkbox("Progressive encoding", id="progressive", value=True)
        with Vertical(id="group-webp"):
            with Horizontal(classes="pair"):
                yield field("Method 0-6", Input(placeholder="6", id="method"))
                yield field("Alpha quality 0-100", Input(placeholder="100", id="alpha-quality"))
            yield Checkbox("Lossless", id="lossless")
        with Vertical(id="group-avif"):
            yield field("Speed 0-10 (slower = smaller)", Input(placeholder="6", id="speed"))
        with Vertical(id="group-oxipng"):
            yield field("Effort 0-6", Input(placeholder="4", id="effort"))
            yield Checkbox("Interlace (Adam7)", id="interlace")
        with Vertical(id="group-pngquant"):
            with Horizontal(classes="pair"):
                yield field("Quality range", Input(placeholder="65-90", id="quality-range"))
                yield field("Speed 1-11", Input(placeholder="4", id="pngquant-speed"))
            yield Checkbox("Dither (Floyd-Steinberg)", id="dither", value=True)
        with Vertical(id="group-gifsicle"):
            with Horizontal(classes="pair"):
                yield field("Optimize level 1-3", Input(placeholder="3", id="optimize-level"))
                yield field("Lossy 0-200", Input(placeholder="0", id="lossy"))

        with Collapsible(title="Sharpen fine-tuning & safety", collapsed=True, id="adv-more"):
            with Horizontal(classes="pair"):
                yield field("Radius", Input(placeholder="2.0", id="sharpen-radius"))
                yield field("Sigma", Input(placeholder="1.0", id="sharpen-sigma"))
                yield field("Threshold", Input(placeholder="0.0", id="sharpen-threshold"))
            yield Checkbox("Replace files that already exist", id="overwrite")

    def prepare(self) -> None:
        self._presets = load_config().compress_presets
        self.query_one("#preset", Select).set_options(
            [("(none — set everything manually)", "")] + [(name, name) for name in sorted(self._presets)]
        )

    def on_mount(self) -> None:
        self._update_codec()
        if self._initial_files is not None or self._initial_preset is not None:
            self.call_after_refresh(self._apply_initial)

    def _apply_initial(self) -> None:
        """Values handed over by another screen (Convert's "Save for web…") win over remembered ones."""
        if self._initial_preset and self._initial_preset in self._presets:
            self.query_one("#preset", Select).value = self._initial_preset
            self._apply_preset(self._initial_preset)
        if self._initial_files is not None:
            self.query_one(FileTarget).value = self._initial_files

    # ------------------------------------------------------------------ preset & codec
    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "codec":
            self._update_codec()
        elif event.select.id == "preset":
            self._apply_preset(event.value)

    def _codec(self) -> str:
        value = self.query_one("#codec", Select).value
        return "mozjpeg" if value in _BLANK else str(value)

    def _update_codec(self) -> None:
        codec = self._codec()
        for name in CODECS:
            self.query_one(f"#group-{name}", Vertical).display = codec == name

    def _set_field(self, key: str, kind: str, value: object) -> None:
        widget = self.query_one(f"#{key}")
        if kind == "checkbox":
            widget.value = bool(value)
        elif kind == "select":
            widget.value = value
        else:
            widget.value = "" if value == "" else str(value)

    def _apply_preset(self, name: str | None) -> None:
        settings = self._presets.get(name or "")
        if settings is None:
            return
        for key, (kind, default) in _FIELD_DEFAULTS.items():
            self._set_field(key, kind, default)
        if settings.get("codec") is not None:
            self.query_one("#codec", Select).value = settings["codec"]
        for key, value in settings.items():
            if key != "codec":
                field_id = key.replace("_", "-")
                kind, _ = _FIELD_DEFAULTS.get(field_id, ("input", ""))
                self._set_field(field_id, kind, value)

    # --------------------------------------------------------------------------- command
    def _number(self, wid: str, label: str, *, whole: bool, low: float, high: float | None) -> str | None:
        raw = self.query_one(f"#{wid}", Input).value.strip()
        if not raw:
            return None
        try:
            value = int(raw) if whole else float(raw)
        except ValueError:
            raise FormError(f"{label} must be a {'whole ' if whole else ''}number, not {raw!r}", wid) from None
        if value < low or (high is not None and value > high):
            raise FormError(f"{label} must be {f'{low:g}-{high:g}' if high is not None else f'at least {low:g}'}", wid)
        return raw

    def argv(self) -> list[str]:
        res = self.query_one(FileTarget).resolve()
        if res.empty:
            raise FormError("choose at least one image — paste a path, drop a file, or use Files… / Folder…", "input-files")
        if res.missing or res.unsupported or res.empty_folders:
            bad = (res.missing or [str(p) for p in res.unsupported] or [str(p) for p in res.empty_folders])[0]
            raise FormError(f"not usable: {bad}" if res.missing or res.unsupported else f"no images in {bad}", "input-files")
        codec = self._codec()
        out = ["--codec", codec]
        max_kb = self._number("max-kb", "Max file size", whole=True, low=1, high=None)
        if max_kb:
            if codec not in MAX_KB_CODECS:
                raise FormError("a max file size needs a codec with a quality setting: JPEG, WebP or AVIF "
                                "(mozjpeg, cwebp, avifenc)", "max-kb")
            out += ["--max-kb", max_kb]
        background = self.query_one("#background", Input).value.strip()
        if background:
            out += ["--background", background]
        for flag, wid, label, whole, low, high in [*_NUMBERS["all"], *_NUMBERS.get(codec, [])]:
            value = self._number(wid, label, whole=whole, low=low, high=high)
            if value:
                out += [flag, value]
        checked = lambda wid: self.query_one(f"#{wid}", Checkbox).value  # noqa: E731
        if codec == "mozjpeg":
            out += ["--subsample", str(self.query_one("#subsample", Select).value)]
            out.append("--progressive" if checked("progressive") else "--baseline")
        elif codec == "webp" and checked("lossless"):
            out.append("--lossless")
        elif codec == "oxipng" and checked("interlace"):
            out.append("--interlace")
        elif codec == "pngquant":
            quality_range = self.query_one("#quality-range", Input).value.strip()
            if quality_range:
                out += ["--quality-range", quality_range]
            out.append("--dither" if checked("dither") else "--no-dither")
        if checked("recursive"):
            out.append("--recursive")
        if checked("overwrite"):
            out.append("--overwrite")
        if any(a.startswith("-") for a in res.args):
            return [*out, "--", *res.args]
        return [*res.args, *out]

    def output_dir(self) -> Path | None:
        for item in self.last_report.get("items", []):
            if item.get("output"):
                return Path(item["output"]).parent
        return None

    def summary_text(self, report: dict[str, Any], code: int, dry: bool) -> str:
        text = super().summary_text(report, code, dry)
        if dry:
            return text
        items = report.get("items", [])
        done = [i for i in items if i.get("status") == "written" and i.get("bytes_before")]
        bigger = sum(1 for i in items if i.get("status") == "not_smaller")
        if done:
            before = sum(i["bytes_before"] for i in done)
            after = sum(i["bytes_after"] for i in done)
            text += f"  ·  {_size(before)} → {_size(after)} ({(1 - after / before) * 100:.0f}% smaller)"
        if bigger:
            text += f"  ·  {bigger} not smaller than the original, left as is"
        return text

    def plan_text(self, report: dict[str, Any]) -> str:
        items = [i for i in report.get("items", []) if i.get("output")]
        if not items:
            return "nothing to do"
        lines = [f"{len(items)} file(s) — compressed copies are written beside the originals"]
        lines += [f"{Path(i['input']).name} → {Path(i['output']).name}" for i in items[:4]]
        if len(items) > 4:
            lines.append(f"… and {len(items) - 4} more")
        return "\n".join(lines)
