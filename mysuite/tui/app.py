from __future__ import annotations

from pathlib import Path

from textual.app import App

from mysuite.tui.screens.home import HomeScreen

_THEME_CSS_PATH = Path(__file__).parent / "theme.css"


class MysuiteApp(App):
    CSS_PATH = str(_THEME_CSS_PATH)
    TITLE = "mysuite"
    BINDINGS = [("q", "quit", "Quit"), ("f1", "helper", "Help")]

    def __init__(self, *, show_welcome: bool = False) -> None:
        super().__init__()
        self._show_welcome = show_welcome

    def on_mount(self) -> None:
        self.push_screen(HomeScreen())
        if self._show_welcome:
            from mysuite.tui.screens.welcome import WelcomeScreen

            self.push_screen(WelcomeScreen())

    def action_helper(self) -> None:
        """The mascot explains whichever tool screen is open (F1 anywhere, h on the overview)."""
        from mysuite.tui.screens.helper import HelperScreen
        from mysuite.tui.screens.welcome import WelcomeScreen

        screen = self.screen
        if isinstance(screen, WelcomeScreen):
            return
        if isinstance(screen, HelperScreen):
            screen.action_close()
            return
        self.push_screen(HelperScreen(getattr(screen, "TOOL_KEY", None)))
