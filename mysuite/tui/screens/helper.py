from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from mysuite.tui import style
from mysuite.tui.registry import TOOL_REGISTRY

GENERAL = (
    "Each card is one job: pick it with the arrow keys and Enter, or press its number.\n"
    "Inside a tool: Esc goes back, Ctrl+R runs, F1 explains the screen you're on.\n"
    "Everything runs on this computer and your originals are never changed.\n"
    "Prefer typing? Every tool is also a command: run `mysuite --help`. AI agents can use them too (see AGENTS.md)."
)


class HelperScreen(ModalScreen[None]):
    """Help for the screen you're on, as a panel on the right over the dimmed screen. F1, h or Esc closes it."""

    BINDINGS = [("escape", "close", "Close"), ("f1", "close", "Close"), ("h", "close", "Close"), ("q", "close", "Close")]

    DEFAULT_CSS = f"""
    HelperScreen {{ align: right top; background: {style.BACKGROUND} 60%; }}
    #helper-panel {{
        width: 64; max-width: 90%; height: 100%;
        background: {style.SURFACE}; border-left: outer {style.ACCENT}; padding: 1 2;
    }}
    #helper-panel > Static {{ background: {style.SURFACE}; width: 100%; }}
    #helper-kicker {{ color: {style.DIM}; text-style: bold; }}
    #helper-title {{ text-style: bold; margin-bottom: 1; }}
    #helper-text {{ color: {style.TEXT}; height: auto; }}
    #helper-cli-label {{ color: {style.DIM}; margin-top: 2; }}
    #helper-cli {{ color: {style.ACCENT}; background: {style.BACKGROUND}; padding: 0 1; height: auto; }}
    #helper-close {{ color: {style.DIM}; dock: bottom; }}
    """

    def __init__(self, tool_key: str | None) -> None:
        super().__init__()
        spec = next((s for s in TOOL_REGISTRY if s.key == tool_key), None)
        self._title = spec.label if spec else "The toolbox"
        self._text = (spec.help if spec and spec.help else GENERAL)
        self._cli = spec.cli if spec else "mysuite --help"

    def compose(self) -> ComposeResult:
        with Vertical(id="helper-panel"):
            yield Static("HELP", id="helper-kicker")
            yield Static(Text(self._title, style=f"bold {style.ACCENT}"), id="helper-title")
            yield Static(Text(self._text), id="helper-text")
            yield Static("Same job in the terminal", id="helper-cli-label")
            yield Static(Text(self._cli), id="helper-cli")
            yield Static("Esc, F1 or h to close", id="helper-close")

    def on_mount(self) -> None:
        panel = self.query_one("#helper-panel")
        panel.styles.opacity = 0.0                    # a short, quiet fade-in
        panel.styles.animate("opacity", value=1.0, duration=0.15)

    def on_click(self, event) -> None:
        if event.widget is self:
            self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)
