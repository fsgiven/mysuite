"""The one-line bar at the top of every screen: where you are on the left, context on the right."""
from __future__ import annotations

import os
from pathlib import Path

from rich.text import Text
from textual.widgets import Static

from mysuite.tui import style


def _short_path(path: str, limit: int = 40) -> str:
    home = str(Path.home())
    if home and home != "/" and path.startswith(home):
        path = "~" + path[len(home):]
    return path if len(path) <= limit else "…" + path[-(limit - 1):]


class AppBar(Static):
    """`mysuite › Export` ··· `profile bild · ~/work · v0.1.0`."""

    DEFAULT_CSS = f"""
    AppBar {{
        dock: top;
        height: 1;
        width: 100%;
        background: {style.SURFACE};
        padding: 0 2;
    }}
    """

    def __init__(self, title: str = "", **kwargs) -> None:
        super().__init__("", **kwargs)
        self._title = title

    def on_mount(self) -> None:
        self.refresh_bar()

    def on_resize(self) -> None:
        self.refresh_bar()

    def set_title(self, title: str) -> None:
        self._title = title
        self.refresh_bar()

    def context(self) -> str:
        from mysuite import profiles

        parts = []
        profile = profiles.active_name()
        if profile:
            parts.append(f"profile {profile}")
        parts.append(_short_path(os.getcwd()))
        parts.append(f"v{style.version()}")
        return "  ·  ".join(parts)

    def refresh_bar(self) -> None:
        left = Text()
        left.append("mysuite", style=f"bold {style.TEXT}")
        if self._title:
            left.append("  /  ", style=style.DIM)
            left.append(self._title, style=f"bold {style.ACCENT}")
        right = Text(self.context(), style=style.DIM)
        width = max(0, self.size.width - 4)
        gap = width - len(left.plain) - len(right.plain)
        line = left + Text(" " * max(2, gap)) + (right if gap >= 2 else Text(""))
        self.update(line)
