from __future__ import annotations

from rich.markup import escape
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from mysuite.tui import art
from mysuite.tui.registry import TOOL_REGISTRY

GENERAL = (
    "This is the toolbox. Each card is one job: pick it with the arrow keys and enter, or press its number.\n"
    "Inside a tool: Esc goes back, Ctrl+R runs, F1 explains the screen you're on.\n"
    "Everything runs on this computer and your originals are never changed.\n"
    "Prefer typing? Every tool is also a command: run `mysuite --help`. AI agents can use them too (see AGENTS.md)."
)


class HelperScreen(ModalScreen[None]):
    """The mascot slides up from the bottom and explains the tool you're in; F1, h or Esc closes it."""

    BINDINGS = [("escape", "close", "Close"), ("f1", "close", "Close"), ("h", "close", "Close"), ("q", "close", "Close")]

    DEFAULT_CSS = """
    HelperScreen { align: left bottom; background: #10121A 55%; }
    #helper-panel {
        height: auto; max-height: 20; width: 100%;
        background: #161822; border-top: heavy #FDE047; padding: 1 2;
    }
    #helper-face { width: 14; height: 6; }
    #helper-body { width: 1fr; height: auto; }
    #helper-title { text-style: bold; margin-bottom: 1; }
    #helper-cli { margin-top: 1; color: #5EEAD4; }
    #helper-close { color: #6B7280; margin-top: 1; }
    """

    def __init__(self, tool_key: str | None) -> None:
        super().__init__()
        spec = next((s for s in TOOL_REGISTRY if s.key == tool_key), None)
        self._title = spec.label if spec else "The toolbox"
        self._accent = spec.accent if spec else "#FDE047"
        self._text = (spec.help if spec and spec.help else GENERAL)
        self._cli = spec.cli if spec else "mysuite --help"
        self._typed = 0
        self._tick = 0

    def compose(self) -> ComposeResult:
        with Horizontal(id="helper-panel"):
            yield Static(art.small_mascot(), id="helper-face")
            with Vertical(id="helper-body"):
                yield Static("", id="helper-title")
                yield Static("", id="helper-text")
                yield Static("", id="helper-cli")
                yield Static("F1, h or Esc to close", id="helper-close")

    def on_mount(self) -> None:
        self.query_one("#helper-title", Static).update(Text(self._title, style=f"bold {self._accent}"))
        self.query_one("#helper-panel").styles.border_top = ("heavy", self._accent)
        self.set_interval(1 / 60, self._type)

    def _type(self) -> None:
        self._tick += 1
        if self._typed < len(self._text):
            self._typed = min(len(self._text), self._typed + 4)
            self.query_one("#helper-text", Static).update(Text(self._text[: self._typed]))
            face = art.FACES["talk"] if (self._tick // 3) % 2 == 0 else art.FACES["open"]
        else:
            face = art.FACES["open"]
            self.query_one("#helper-cli", Static).update(Text("same thing in the terminal:  " + self._cli) if self._cli else "")
        self.query_one("#helper-face", Static).update(art.small_mascot(face))

    def action_close(self) -> None:
        self.dismiss(None)
