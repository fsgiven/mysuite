"""Choosing the files a tool works on: one box that takes paths, folders, globs and drops, and says at once what it found."""
from __future__ import annotations

import glob
import os
from dataclasses import dataclass, field
from pathlib import Path

from rich.markup import escape
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.suggester import Suggester
from textual.widgets import Button, Input, OptionList, Static
from textual.widgets.option_list import Option

from mysuite.tui import ui_state
from mysuite.tui.dragdrop import merge_paths_into_input, parse_dropped_paths, parse_input_files_field
from mysuite.utils.paths import show_path

_GLOB_CHARS = set("*?[")


@dataclass
class Resolution:
    """What a files box currently points at."""
    files: list[Path] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    unsupported: list[Path] = field(default_factory=list)
    empty_folders: list[Path] = field(default_factory=list)
    empty: bool = True                      # the box has no text at all
    args: list[str] = field(default_factory=list)   # what to hand the command: files and folders as typed, globs expanded

    @property
    def ok(self) -> bool:
        return bool(self.files) and not self.missing


def resolve_targets(value: str, extensions: frozenset[str] | None, *, recursive: bool = False) -> Resolution:
    """Turn the box's text into files. Items are comma separated (a single path containing a comma still works);
    each is a file, a folder (its matching files), or a glob such as ``~/Pictures/*.png``."""
    res = Resolution(empty=not value.strip())
    seen: set[Path] = set()

    def keep(path: Path) -> None:
        key = path.resolve()
        if key not in seen:
            seen.add(key)
            res.files.append(path)

    def wanted(path: Path) -> bool:
        return extensions is None or path.suffix.lower() in extensions

    for raw in parse_input_files_field(value):
        item = Path(os.path.expanduser(str(raw)))
        text = str(item)
        if item.is_dir():
            res.args.append(text)
            pattern = "**/*" if recursive else "*"
            found = sorted(p for p in item.glob(pattern) if p.is_file() and not p.name.startswith(".") and wanted(p))
            if found:
                for path in found:
                    keep(path)
            else:
                res.empty_folders.append(item)
        elif item.is_file():
            if wanted(item):
                keep(item)
                res.args.append(text)
            else:
                res.unsupported.append(item)
        elif _GLOB_CHARS & set(text):
            hits = sorted(Path(p) for p in glob.glob(text, recursive=True))
            matched = [p for p in hits if p.is_file() and wanted(p)]
            if matched:
                for path in matched:
                    keep(path)
                    res.args.append(str(path))
            else:
                res.missing.append(text)
        else:
            res.missing.append(text)
    return res


def describe(res: Resolution, noun: str) -> tuple[str, str]:
    """(markup, level) for the status line under the box; level is ok / warn / error / hint."""
    if res.empty:
        return ("Paste a path, drop files here, type a folder or a glob like ~/Pictures/*.png — or use the buttons.", "hint")
    parts: list[str] = []
    level = "ok"
    if res.files:
        names = ", ".join(escape(p.name) for p in res.files[:3])
        more = f", +{len(res.files) - 3}" if len(res.files) > 3 else ""
        parts.append(f"✓ {len(res.files)} {noun}{'s' if len(res.files) != 1 else ''} · {names}{more}")
    for key, label in (("missing", "not found"), ("unsupported", "not a supported type"), ("empty_folders", "no matching files in")):
        items = getattr(res, key)
        if items:
            level = "warn" if res.files else "error"
            shown = ", ".join(escape(show_path(Path(i)) if not isinstance(i, str) else i) for i in items[:2])
            parts.append(f"✗ {label}: {shown}{'…' if len(items) > 2 else ''}")
    if not res.files and not parts:
        level = "error"
        parts.append("✗ nothing found")
    return ("   ".join(parts), level)


class PathSuggester(Suggester):
    """Inline completion of the last path in the box: press → (or End) to accept."""

    def __init__(self) -> None:
        super().__init__(use_cache=False, case_sensitive=True)

    async def get_suggestion(self, value: str) -> str | None:
        head, sep, tail = value.rpartition(",")
        token = tail.lstrip()
        lead = tail[: len(tail) - len(token)]
        if not token or not (token[0] in "/~." or "/" in token):
            return None
        expanded = os.path.expanduser(token)
        folder, _, prefix = expanded.rpartition("/")
        try:
            entries = sorted(os.listdir(folder or "/"))
        except OSError:
            return None
        for name in entries:
            if name.startswith(prefix) and (prefix.startswith(".") or not name.startswith(".")) and name != prefix:
                full = os.path.join(folder or "/", name)
                completion = name[len(prefix):] + ("/" if os.path.isdir(full) else "")
                return head + sep + lead + token + completion
        return None


class DropInput(Input):
    """An Input that takes a dragged-in file: terminals turn an OS file drop into a Paste of its path. Textual
    runs `_on_paste` of every class in the MRO, so Input's own insert would duplicate the text unless we
    `prevent_default()`. Only a paste that resolves to real files/folders is merged; other text pastes normally."""

    def __init__(self, *args, extensions: frozenset[str] | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._extensions = extensions

    def _on_paste(self, event: events.Paste) -> None:
        dropped = parse_dropped_paths(event.text, extensions=self._extensions)
        if dropped:
            self.value = merge_paths_into_input(self.value, dropped)
            self.cursor_position = len(self.value)
            event.stop()
            event.prevent_default()


class RecentScreen(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "dismiss(None)", "Close")]

    def __init__(self, paths: list[str]) -> None:
        super().__init__()
        self._paths = paths

    def compose(self) -> ComposeResult:
        with Vertical(id="recent-dialog"):
            yield Static("Recently used", id="recent-title")
            yield OptionList(*[Option(show_path(Path(p)), id=str(i)) for i, p in enumerate(self._paths)], id="recent-list")
            yield Static("[dim]Enter adds it · Esc closes[/dim]", id="recent-hint")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(self._paths[int(event.option.id)])


class FileTarget(Vertical):
    """Input + Files… / Folder… / Recent buttons + a live status line.

    The Input keeps the id you give it (tools and tests read `#input-files`-style ids as before). Posts
    `FileTarget.Changed` with the current Resolution whenever the text changes.
    """

    class Changed(Message):
        def __init__(self, target: "FileTarget", resolution: Resolution) -> None:
            super().__init__()
            self.target = target
            self.resolution = resolution

    def __init__(
        self,
        *,
        input_id: str,
        browse_id: str,
        extensions: frozenset[str] | None,
        noun: str = "file",
        placeholder: str = "path, folder, or glob",
        recent_kind: str = "files",
        recursive: bool = False,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id, classes="file-target")
        self._input_id = input_id
        self._browse_id = browse_id
        self.extensions = extensions
        self.noun = noun
        self._placeholder = placeholder
        self._recent_kind = recent_kind
        self.recursive = recursive
        self.resolution = Resolution()

    def compose(self) -> ComposeResult:
        with Horizontal(classes="field-row"):
            yield DropInput(placeholder=self._placeholder, id=self._input_id, extensions=self.extensions, suggester=PathSuggester())
            yield Button("Files…", id=self._browse_id, classes="mini")
            yield Button("Folder…", id=f"{self._browse_id}-folder", classes="mini")
            yield Button("Recent", id=f"{self._browse_id}-recent", classes="mini")
        yield Static("", id=f"{self._input_id}-status", classes="target-status")

    def on_mount(self) -> None:
        self._refresh()

    # ----------------------------------------------------------------------------- state
    @property
    def input(self) -> Input:
        return self.query_one(f"#{self._input_id}", Input)

    @property
    def value(self) -> str:
        return self.input.value

    @value.setter
    def value(self, text: str) -> None:
        self.input.value = text

    def resolve(self) -> Resolution:
        return resolve_targets(self.input.value, self.extensions, recursive=self.recursive)

    def _refresh(self) -> None:
        self.resolution = self.resolve()
        text, level = describe(self.resolution, self.noun)
        color = {"ok": "#4ADE80", "warn": "#FBBF24", "error": "#F87171", "hint": "#6B7280"}[level]
        self.query_one(f"#{self._input_id}-status", Static).update(f"[{color}]{text}[/{color}]")
        self.post_message(self.Changed(self, self.resolution))

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == self._input_id:
            event.input.remove_class("field-error")
            self._refresh()

    def remember(self) -> None:
        """Call after a successful run: puts these inputs at the top of Recent."""
        ui_state.add_recent([str(p) for p in parse_input_files_field(self.input.value)], self._recent_kind)

    # --------------------------------------------------------------------------- buttons
    def _add(self, path: Path | None) -> None:
        if path is not None:
            self.value = merge_paths_into_input(self.value, [path])
            self.input.cursor_position = len(self.value)
            self.input.focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        from mysuite.tui.screens.file_picker import FilePickerScreen

        bid = event.button.id
        if bid == self._browse_id:
            event.stop()
            self.app.push_screen(FilePickerScreen(title=f"Choose {self.noun}s"), self._add)
        elif bid == f"{self._browse_id}-folder":
            event.stop()
            self.app.push_screen(FilePickerScreen(pick_directories=True, title="Choose a folder"), self._add)
        elif bid == f"{self._browse_id}-recent":
            event.stop()
            recent = ui_state.recent_paths(self._recent_kind)
            if not recent:
                self.query_one(f"#{self._input_id}-status", Static).update("[#6B7280]Nothing used yet — your last inputs will show up here.[/#6B7280]")
                return
            self.app.push_screen(RecentScreen(recent), lambda p: self._add(Path(p)) if p else None)
