from __future__ import annotations

from pathlib import Path

from textual.app import App

from mysuite.tui.screens.home import HomeScreen

_THEME_CSS_PATH = Path(__file__).parent / "theme.css"


class MysuiteApp(App):
    CSS_PATH = str(_THEME_CSS_PATH)
    TITLE = "mysuite"
    BINDINGS = [("q", "quit", "Quit")]

    def on_mount(self) -> None:
        self.push_screen(HomeScreen())
