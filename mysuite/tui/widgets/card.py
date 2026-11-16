from __future__ import annotations

import textwrap

from rich.text import Text
from textual.events import Click
from textual.message import Message
from textual.widgets import Static

from mysuite.tui import style
from mysuite.tui.registry import ToolSpec


def two_lines(text: str, width: int) -> str:
    """At most two lines, broken between words; a cut ends in an ellipsis after a whole word."""
    lines = textwrap.wrap(text, width) or [""]
    if len(lines) <= 2:
        return "\n".join(lines)
    words = lines[1].split()
    while words and len(" ".join(words)) + 1 > width:
        words.pop()
    last = " ".join(words).rstrip(",;:") + "…"
    return lines[0] + "\n" + last


class ToolCard(Static, can_focus=True):
    """One tool: name (with its number key) and a two-line description. Calm by default, accent bar when focused."""

    DEFAULT_CSS = f"""
    ToolCard {{
        height: 4;
        width: 1fr;
        padding: 0 1 0 1;
        background: {style.SURFACE};
        border-left: blank {style.SURFACE};
    }}
    ToolCard:hover {{ background: #1A1E2C; }}
    ToolCard:focus {{ background: {style.SURFACE_HI}; border-left: outer {style.ACCENT}; }}
    """

    class Chosen(Message):
        def __init__(self, key: str) -> None:
            super().__init__()
            self.key = key

    def __init__(self, spec: ToolSpec, number: int | None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.spec = spec
        self.number = number

    def on_mount(self) -> None:
        self._render_card()
        self.call_after_refresh(self._render_card)

    def on_resize(self) -> None:
        self._render_card()

    def _render_card(self) -> None:
        width = max(10, self.content_size.width)
        title = Text(self.spec.label, style=f"bold {style.TEXT}")
        if self.number is not None:
            tag = f" {self.number} "
            title.append(" " * max(1, width - len(self.spec.label) - len(tag)))
            title.append(tag, style=f"{style.DIM} on #232838")
        self.update(Text.assemble(title, "\n", Text(two_lines(self.spec.description, width), style=style.MUTED)))

    def on_click(self, event: Click) -> None:
        self.focus()
        self.post_message(self.Chosen(self.spec.key))
