from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button, Input, Label


class RecolorRow(Horizontal):
    """One FROM -> TO color-swap row in the Recolor field-group: two Inputs
    (hex or named CSS color, either side) and a remove button. Owns its own
    remove button — stops the Button.Pressed event before it bubbles, so the
    containing ExportScreen never needs to know per-row buttons exist, only
    the fixed "+ Add color swap" button."""

    def __init__(self, from_value: str = "", to_value: str = "") -> None:
        super().__init__(classes="field-row recolor-row")
        self.from_input = Input(value=from_value, placeholder="white or #ffffff")
        self.to_input = Input(value=to_value, placeholder="#ff0000 or black")

    def compose(self) -> ComposeResult:
        yield self.from_input
        yield Label("→")
        yield self.to_input
        yield Button("−", classes="recolor-remove-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.remove()
