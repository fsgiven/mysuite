from __future__ import annotations

from rich.text import Text
from textual.events import Click
from textual.message import Message
from textual.widgets import Static

from mysuite.tui import art
from mysuite.tui.registry import ToolSpec

NEUTRAL = "#2A2E3F"


class ToolCard(Static, can_focus=True):
    """One tool as a card: icon, name, one-line description, in the tool's accent colour."""

    DEFAULT_CSS = """
    ToolCard {
        height: 11;
        width: 1fr;
        padding: 0 1;
        background: #161822;
        border: round #2A2E3F;
    }
    ToolCard:hover { background: #1A1E2C; }
    ToolCard:focus { background: #1D2333; }
    """

    class Chosen(Message):
        def __init__(self, key: str) -> None:
            super().__init__()
            self.key = key

    def __init__(self, spec: ToolSpec, number: int | None, **kwargs) -> None:
        super().__init__(self._build(spec, number), **kwargs)
        self.spec = spec

    @staticmethod
    def _build(spec: ToolSpec, number: int | None) -> Text:
        text = Text()
        for row in art.icon_rows(spec.key):
            text.append(row + "\n", style=spec.accent)
        text.append("\n")
        text.append(spec.label, style=f"bold {spec.accent}")
        if number is not None:
            text.append(f"  {number}", style="#6B7280")
        text.append("\n")
        text.append(spec.description, style="#8B93A7")
        return text

    def on_focus(self) -> None:
        self.styles.border = ("round", self.spec.accent)

    def on_blur(self) -> None:
        self.styles.border = ("round", NEUTRAL)

    def on_click(self, event: Click) -> None:
        self.focus()
        self.post_message(self.Chosen(self.spec.key))
