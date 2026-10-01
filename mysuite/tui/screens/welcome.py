from __future__ import annotations

from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import Center, Middle, Vertical
from textual.screen import Screen
from textual.widgets import Static

# The mascot: a suitcase with a face. Two frames (eyes open / blinking).
_BODY = """\
        ╭──────╮
        │      │
  ╭─────┴──────┴─────╮
  │   {l}      {r}   │
  │       {m}        │
  ├──[▪]────────[▪]──┤
  │    m y s u i t e │
  ╰──┬────────────┬──╯
     ╰╯          ╰╯"""
_OPEN = ("●", "●", "‿")
_BLINK = ("─", "─", "‿")
_WINK = ("●", "─", "◡")

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
        self.query_one("#mascot", Static).update(
            f"[#FDE047]{escape(_BODY.format(l=l, r=r, m=m))}[/]"
        )
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
