from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DirectoryTree, Static


class FilePickerScreen(ModalScreen[Path | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(
        self,
        *,
        start_path: Path | None = None,
        pick_directories: bool = False,
        title: str = "Choose a file",
    ) -> None:
        super().__init__()
        self.start_path = start_path or Path.cwd()
        self.pick_directories = pick_directories
        self._title = title

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-dialog"):
            yield Static(self._title, id="picker-title")
            yield DirectoryTree(str(self.start_path), id="picker-tree")
            with Horizontal(id="picker-buttons"):
                if self.pick_directories:
                    yield Button("Select this folder", id="pick-current", variant="primary")
                yield Button("Cancel", id="cancel")

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        if not self.pick_directories:
            self.dismiss(Path(event.path))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
        elif event.button.id == "pick-current":
            tree = self.query_one(DirectoryTree)
            node = tree.cursor_node
            if node is not None and node.data is not None:
                self.dismiss(Path(node.data.path))
            else:
                self.dismiss(self.start_path)

    def action_cancel(self) -> None:
        self.dismiss(None)
