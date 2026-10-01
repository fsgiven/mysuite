from __future__ import annotations

from pathlib import Path
from typing import Iterable

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DirectoryTree, Input, Static

from mysuite.utils.paths import show_path


class _VisibleDirectoryTree(DirectoryTree):
    """Hides dotfiles/dot-folders (.git, .DS_Store, ...) — they're clutter when
    you're looking for a photo."""

    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        return [p for p in paths if not p.name.startswith(".")]


class FilePickerScreen(ModalScreen[Path | None]):
    """Opens at the folder last used with this same picker (by title) in this
    session, else the home folder — not the project folder. Jump buttons and a
    paste-a-path box reach anywhere on disk; the last-used folder is kept in
    memory only (never written to disk)."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(
        self,
        *,
        start_path: Path | None = None,
        pick_directories: bool = False,
        title: str = "Choose a file",
    ) -> None:
        super().__init__()
        self._explicit_start = start_path
        self.pick_directories = pick_directories
        self._title = title
        self.start_path = self._initial_dir()

    def _memory(self) -> dict[str, Path]:
        app = self.app
        if not hasattr(app, "picker_memory"):
            app.picker_memory = {}
        return app.picker_memory

    def _initial_dir(self) -> Path:
        if self._explicit_start is not None:
            return self._explicit_start
        try:
            remembered = self._memory().get(self._title)
        except Exception:
            remembered = None
        if remembered is not None and remembered.is_dir():
            return remembered
        return Path.home()

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-dialog"):
            yield Static(self._title, id="picker-title")
            with Horizontal(id="picker-jumps"):
                yield Button("↑ Parent", id="jump-parent")
                yield Button("Home", id="jump-home")
                yield Button("Desktop", id="jump-desktop")
                yield Button("Downloads", id="jump-downloads")
                yield Button("/ Root", id="jump-root")
            yield Input(placeholder="Type or paste a path, then Enter", id="picker-path")
            yield Static("", id="picker-current")
            yield _VisibleDirectoryTree(str(self.start_path), id="picker-tree")
            with Horizontal(id="picker-buttons"):
                if self.pick_directories:
                    yield Button("Select this folder", id="pick-current", variant="primary")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self._show_current()
        self._tree.focus()

    @property
    def _tree(self) -> DirectoryTree:
        return self.query_one("#picker-tree", DirectoryTree)

    def _current_dir(self) -> Path:
        return Path(self._tree.path)

    def _show_current(self) -> None:
        self.query_one("#picker-current", Static).update(f"[dim]{show_path(self._current_dir())}[/dim]")

    def _go(self, target: Path) -> None:
        if not target.is_dir():
            self.query_one("#picker-current", Static).update(f"[#F87171]not a folder: {show_path(target)}[/#F87171]")
            return
        self._tree.path = target
        self._show_current()

    def _finish(self, chosen: Path | None) -> None:
        if chosen is not None:
            folder = chosen if chosen.is_dir() else chosen.parent
            self._memory()[self._title] = folder
        self.dismiss(chosen)

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        if not self.pick_directories:
            self._finish(Path(event.path))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        raw = event.value.strip().strip("'\"")
        if not raw:
            return
        target = Path(raw).expanduser()
        if target.is_dir():
            self._go(target)
        elif target.is_file() and not self.pick_directories:
            self._finish(target)
        else:
            self.query_one("#picker-current", Static).update(f"[#F87171]not found: {show_path(target)}[/#F87171]")

    _JUMPS = {
        "jump-home": lambda: Path.home(),
        "jump-desktop": lambda: Path.home() / "Desktop",
        "jump-downloads": lambda: Path.home() / "Downloads",
        "jump-root": lambda: Path("/"),
    }

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "cancel":
            self.dismiss(None)
        elif button_id == "jump-parent":
            self._go(self._current_dir().parent)
        elif button_id in self._JUMPS:
            self._go(self._JUMPS[button_id]())
        elif button_id == "pick-current":
            node = self._tree.cursor_node
            if node is not None and node.data is not None:
                picked = Path(node.data.path)
                self._finish(picked if picked.is_dir() else picked.parent)
            else:
                self._finish(self._current_dir())

    def action_cancel(self) -> None:
        self.dismiss(None)
