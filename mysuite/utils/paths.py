from __future__ import annotations

from pathlib import Path

from rich.markup import escape


def tilde(path: Path | str) -> str:
    """Shows a path under the home folder as ~/...: shorter, and it keeps your
    username out of screenshots and screen shares."""
    text = str(path)
    home = str(Path.home())
    if text == home:
        return "~"
    if text.startswith(home + "/"):
        return "~" + text[len(home):]
    return text


def show_path(path: Path | str) -> str:
    """tilde() plus Rich-markup escaping, for putting a path in a RichLog. File
    names are untrusted input: without escaping, one containing "[/bold]" raises
    MarkupError and one containing "[link=...]" injects a clickable link."""
    return escape(tilde(path))
