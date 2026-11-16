from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Grid, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Static

from mysuite.tui.registry import GROUPS, TOOL_REGISTRY
from mysuite.tui.widgets.app_bar import AppBar
from mysuite.tui.widgets.card import ToolCard

CARD_MIN_WIDTH = 30
MAX_COLUMNS = 4
FADE_SECONDS = 0.25
FADE_STAGGER = 0.012


class HomeScreen(Screen):
    """The tool overview: calm cards under group headings. Arrows move by position, enter opens, 1-9 jump."""

    BINDINGS = [("enter", "open", "Open"), ("h", "helper", "Help")]

    def compose(self) -> ComposeResult:
        yield AppBar("Tools")
        with VerticalScroll(id="cards-scroll"):
            number = 0
            for group in GROUPS:
                specs = [s for s in TOOL_REGISTRY if s.group == group]
                if not specs:
                    continue
                yield Static(group.upper(), classes="group-title")
                with Grid(classes="cards"):
                    for spec in specs:
                        number += 1
                        yield ToolCard(spec, number if number <= 9 else None, id=f"tool-{spec.key}")
        yield Footer(show_command_palette=False)

    def on_mount(self) -> None:
        self.app.sub_title = "Tools"
        self._columns = 3
        cards = list(self.query(ToolCard))
        for i, card in enumerate(cards):              # a short, quiet fade-in; nothing to wait for
            card.styles.opacity = 0.0
            card.styles.animate("opacity", value=1.0, duration=FADE_SECONDS, delay=i * FADE_STAGGER)
        cards[0].focus()

    # --------------------------------------------------------------- layout
    def on_resize(self) -> None:
        width = self.size.width
        self._columns = max(1, min(MAX_COLUMNS, (width - 6) // CARD_MIN_WIDTH))
        for grid in self.query(".cards").results(Grid):
            grid.styles.grid_size_columns = self._columns

    # ------------------------------------------------------------- navigation
    def _neighbour(self, card: ToolCard, key: str) -> ToolCard | None:
        """The nearest card in that direction, by position on screen (works across group headings)."""
        here = card.region
        best, best_score = None, None
        for other in self.query(ToolCard):
            if other is card:
                continue
            r = other.region
            if key in ("left", "right"):
                if r.y != here.y or (r.x < here.x if key == "right" else r.x > here.x):
                    continue
                score = (abs(r.x - here.x), 0)
            else:
                dy = r.y - here.y
                if (dy <= 0 if key == "down" else dy >= 0):
                    continue
                score = (abs(dy), abs(r.x - here.x))
            if best_score is None or score < best_score:
                best, best_score = other, score
        return best

    def on_key(self, event) -> None:
        key = event.key
        cards = list(self.query(ToolCard))
        focused = next((c for c in cards if c.has_focus), cards[0])
        if key in ("left", "right", "up", "down"):
            target = self._neighbour(focused, key)
            if target is not None:
                target.focus()
                target.scroll_visible()
            event.stop()
        elif key.isdigit() and key != "0":
            index = int(key) - 1
            if 0 <= index < len(cards):
                self._open_tool(cards[index].spec.key)
                event.stop()

    def action_open(self) -> None:
        focused = next((c for c in self.query(ToolCard) if c.has_focus), None)
        if focused is not None:
            self._open_tool(focused.spec.key)

    def on_tool_card_chosen(self, message: ToolCard.Chosen) -> None:
        self._open_tool(message.key)

    def _open_tool(self, key: str) -> None:
        spec = next((s for s in TOOL_REGISTRY if s.key == key), None)
        if spec is not None:
            self.app.push_screen(spec.screen_factory())

    def action_helper(self) -> None:
        self.app.action_helper()

