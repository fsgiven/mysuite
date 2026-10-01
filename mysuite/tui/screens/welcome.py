from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import Screen

from mysuite.tui.widgets.scene import Scene

TIPS = [
    "Hi! I carry your image tools so you don't have to.",
    "Export a logo to every size and format in one go.",
    "Ask for 500 px and you get exactly 500 px. No review needed.",
    "Everything runs on this computer. No accounts, no cloud.",
    "Strip or randomise metadata before you share a photo.",
    "Press h or F1 on any screen and I'll explain it.",
    "AI agents can use me too: see AGENTS.md.",
]


class WelcomeScreen(Screen):
    """Front page: an animated scene with the suitcase; any key continues to the tool overview."""

    def compose(self) -> ComposeResult:
        yield Scene(TIPS, id="scene")

    def on_mount(self) -> None:
        self.app.sub_title = "Welcome"

    def on_key(self, event) -> None:
        event.stop()
        self.dismiss()

    def on_click(self) -> None:
        self.dismiss()
