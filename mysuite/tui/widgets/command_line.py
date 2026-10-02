"""The exact `mysuite …` command a form would run — copyable, so people and agents can repeat it without the TUI."""
from __future__ import annotations

import shlex

from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button, Static


def format_command(argv: list[str]) -> str:
    return "mysuite " + " ".join(shlex.quote(a) for a in argv)


class CommandLine(Horizontal):
    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id, classes="command-line")
        self.command = ""

    def compose(self) -> ComposeResult:
        yield Static("", classes="command-text")
        yield Button("Copy", classes="mini copy-btn")

    def show(self, argv: list[str] | None, note: str = "") -> None:
        self.command = format_command(argv) if argv else ""
        text = escape(self.command) if self.command else f"[dim]{escape(note or 'fill in the form to see the command')}[/dim]"
        self.query_one(".command-text", Static).update(text)
        self.query_one(".copy-btn", Button).disabled = not self.command

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if self.command:
            event.stop()
            self.app.copy_to_clipboard(self.command)
            self.app.notify("Command copied", timeout=2)
