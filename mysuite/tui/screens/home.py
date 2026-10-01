from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static

from mysuite.tui.registry import TOOL_REGISTRY


class HomeScreen(Screen):
    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="tool-list-container"):
            yield ListView(
                *[
                    ListItem(
                        Label(f"{i}. [{spec.accent}]{spec.label}[/]  —  {spec.description}"),
                        id=f"tool-{spec.key}",
                    )
                    for i, spec in enumerate(TOOL_REGISTRY, start=1)
                ],
                id="tool-list",
            )
            yield Static(
                "↑/↓ browse · enter open · number jump · q quit",
                id="hint-bar",
            )
        yield Footer()

    def on_mount(self) -> None:
        self.app.sub_title = "Tools"
        self.query_one("#tool-list", ListView).border_title = "Pick a tool"
        self.query_one("#tool-list", ListView).focus()

    def on_key(self, event) -> None:
        if event.key.isdigit():
            index = int(event.key) - 1
            if 0 <= index < len(TOOL_REGISTRY):
                self._open_tool(TOOL_REGISTRY[index].key)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.item.id is None:
            return
        self._open_tool(event.item.id.removeprefix("tool-"))

    def _open_tool(self, key: str) -> None:
        spec = next((s for s in TOOL_REGISTRY if s.key == key), None)
        if spec is not None:
            self.app.push_screen(spec.screen_factory())
