from __future__ import annotations

from rich.markup import escape
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Center, Middle, Vertical
from textual.screen import Screen
from textual.widgets import Static

# The mascot: a suitcase in a top hat, with eyebrows, cheeks, latches, a travel sticker and
# little feet. Built from fixed-width rows so every line is exactly _W wide (the hat must sit
# centred on the lid whatever the face does). Frames: eyes open / blinking / wink.
_W = 34   # outer width of the case
_IN = _W - 2


def _art(l: str, r: str, m: str) -> list[str]:
    def pad(s: str) -> str:
        return s.center(_W)

    def inner(s: str) -> str:
        return "│" + s.center(_IN) + "│"

    return [
        pad("▄" * 13),
        pad("█" + "▒" * 11 + "█"),
        pad("█" + "▓" * 11 + "█"),
        pad("▄▄▄██" + "▀" * 11 + "██▄▄▄"),
        "╭" + "─" * _IN + "╮",
        inner("╲" + " " * 12 + "╱"),
        inner(l + " " * 12 + r),
        inner("◖◗" + " " * 4 + m + " " * 4 + "◖◗"),
        "├──[▪]" + "─" * (_IN - 10) + "[▪]──┤",
        inner("»  m y s u i t e   ┃ a-z ┃"),
        "╰───┬" + "─" * (_IN - 9) + "┬────╯",
        "    ╰╯" + " " * (_W - 12) + "╰╯    ",
    ]


_OPEN = ("●", "●", "‿")
_BLINK = ("─", "─", "‿")
_WINK = ("●", "─", "◡")

_HAT = "#C4B5FD"       # lilac, same family as Convert's accent
_CASE = "#FDE047"
_CHEEK = "#F9A8D4"
_SKY = "#7DD3FC"
_EYE = "#FFFFFF"


def _colour(ch: str, row: int) -> str:
    if row <= 3:
        return _HAT if ch in "▄█▀▒▓" else _CASE
    if ch in "◖◗":
        return _CHEEK
    if ch in "●─‿◡╲╱" and 5 <= row <= 7:
        return _EYE
    if ch == "»":
        return _SKY
    return _CASE


def render_mascot(l: str, r: str, m: str) -> Text:
    text = Text()
    for row, line in enumerate(_art(l, r, m)):
        for ch in line:
            text.append(ch, style=_colour(ch, row))
        if row < 11:
            text.append("\n")
    return text


TIPS = [
    "Hi! I carry your image tools so you don't have to.",
    "Export a logo to every size and format in one go.",
    "Convert, watermark, cut out, compress, enhance — all offline.",
    "Nothing leaves this computer. No accounts, no cloud.",
    "Strip or randomise metadata before you share a photo.",
    "Press a number on the next screen to jump straight to a tool.",
]


class WelcomeScreen(Screen):
    """Front page: the suitcase says hello; any key continues to the tool overview."""

    def compose(self) -> ComposeResult:
        with Middle(), Center(), Vertical(id="welcome-box"):
            yield Static("", id="mascot")
            yield Static("", id="bubble")
            yield Static("press any key to open the toolbox", id="welcome-hint")

    def on_mount(self) -> None:
        self.app.sub_title = "Welcome"
        self._tip = 0
        self._tick = 0
        self._draw()
        self.set_interval(0.18, self._animate)

    def _frame(self) -> tuple[str, str, str]:
        # blink every ~3s; a wink on every other tip change
        phase = self._tick % 17
        if phase in (15, 16):
            return _WINK if self._tip % 2 else _BLINK
        return _OPEN

    def _draw(self) -> None:
        l, r, m = self._frame()
        self.query_one("#mascot", Static).update(render_mascot(l, r, m))
        text = escape(TIPS[self._tip % len(TIPS)])
        self.query_one("#bubble", Static).update(f"[b]( {text} )[/b]")

    def _animate(self) -> None:
        self._tick += 1
        if self._tick % 28 == 0:  # new tip every ~5s
            self._tip += 1
        self._draw()

    def on_key(self, event) -> None:
        event.stop()
        self.dismiss()
