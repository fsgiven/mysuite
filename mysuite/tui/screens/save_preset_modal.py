from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static


class SavePresetModal(ModalScreen[str | None]):
    def compose(self) -> ComposeResult:
        with Vertical(id="picker-dialog"):
            yield Static("Save current settings as preset", id="picker-title")
            yield Input(placeholder="preset name (e.g. my-web-icons)", id="preset-name-input")
            with Horizontal(id="picker-buttons"):
                yield Button("Save", id="save", variant="primary")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#preset-name-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._save()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self._save()
        elif event.button.id == "cancel":
            self.dismiss(None)

    def _save(self) -> None:
        name = self.query_one("#preset-name-input", Input).value.strip()
        self.dismiss(name or None)
