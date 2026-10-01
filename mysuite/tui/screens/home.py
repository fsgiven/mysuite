from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Grid, Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Static

from mysuite.tui import art
from mysuite.tui.registry import TOOL_REGISTRY
from mysuite.tui.widgets.card import ToolCard

CARD_MIN_WIDTH = 30
MAX_COLUMNS = 4


class HomeScreen(Screen):
    """The tool overview: one card per tool (icon, name, one line), a helper mascot at the bottom."""

    BINDINGS = [("h", "helper", "Help")]

    def compose(self) -> ComposeResult:
        yield Static("[b #E8E8E8]m y s u i t e[/]  [#8B93A7]pick a tool[/]", id="home-title")
        with VerticalScroll(id="cards-scroll"):
            with Grid(id="cards"):
                for i, spec in enumerate(TOOL_REGISTRY, start=1):
                    yield ToolCard(spec, i if i <= 9 else None, id=f"tool-{spec.key}")
        with Horizontal(id="helper-bar"):
            yield Static(art.small_mascot(), id="helper-mascot")
            yield Static(
                "[b]h[/b] or [b]F1[/b] explain this screen  ·  arrows move  ·  enter opens  ·  1-9 jump  ·  q quit",
                id="helper-hint",
            )
        yield Footer()

    def on_mount(self) -> None:
        self.app.sub_title = "Tools"
        self._columns = 3
        self.query_one(ToolCard).focus()

    # --------------------------------------------------------------- layout
    def on_resize(self) -> None:
        width = self.size.width
        self._columns = max(1, min(MAX_COLUMNS, (width - 4) // CARD_MIN_WIDTH))
        self.query_one("#cards", Grid).styles.grid_size_columns = self._columns

    # ------------------------------------------------------------- navigation
    def on_key(self, event) -> None:
        key = event.key
        cards = list(self.query(ToolCard))
        focused = next((i for i, c in enumerate(cards) if c.has_focus), 0)
        step = {"left": -1, "right": 1, "up": -self._columns, "down": self._columns}.get(key)
        if step is not None:
            target = focused + step
            if 0 <= target < len(cards):
                cards[target].focus()
                cards[target].scroll_visible()
            event.stop()
        elif key == "enter":
            self._open_tool(cards[focused].spec.key)
            event.stop()
        elif key.isdigit():
            index = int(key) - 1
            if 0 <= index < len(TOOL_REGISTRY):
                self._open_tool(TOOL_REGISTRY[index].key)

    def on_tool_card_chosen(self, message: ToolCard.Chosen) -> None:
        self._open_tool(message.key)

    def _open_tool(self, key: str) -> None:
        spec = next((s for s in TOOL_REGISTRY if s.key == key), None)
        if spec is not None:
            self.app.push_screen(spec.screen_factory())

    def action_helper(self) -> None:
        self.app.action_helper()
