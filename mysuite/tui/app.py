from __future__ import annotations

from pathlib import Path

from textual.app import App

from mysuite.tui.screens.home import HomeScreen

_THEME_CSS_PATH = Path(__file__).parent / "theme.css"


class MysuiteApp(App):
    CSS_PATH = str(_THEME_CSS_PATH)
    TITLE = "mysuite"
    BINDINGS = [("q", "quit", "Quit")]

    def __init__(self, *, show_welcome: bool = False) -> None:
        super().__init__()
        self._show_welcome = show_welcome

    def on_mount(self) -> None:
        self.push_screen(HomeScreen())
        if self._show_welcome:
            from mysuite.tui.screens.welcome import WelcomeScreen

            self.push_screen(WelcomeScreen())
