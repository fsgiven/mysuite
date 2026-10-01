"""A dashboard screen generated from the CLI itself.

Every command already describes its arguments and options (mysuite.schema), so a tool that has no hand-built
screen gets one for free: a form with one field per option, a Run button, and the command's own output in the
log. It runs the real command (`python -m mysuite <command> …`) so the screen and the CLI can never disagree.
"""
from __future__ import annotations

import os
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

from rich.markup import escape
from rich.text import Text
from textual import events, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Checkbox, Collapsible, Footer, Header, Input, Label, RichLog, Select, Static

from mysuite import schema
from mysuite.pipeline import engine
from mysuite.tui.dragdrop import merge_paths_into_input, parse_dropped_paths, parse_input_files_field
from mysuite.tui.registry import ToolSpec
from mysuite.tui.screens.file_picker import FilePickerScreen

HIDDEN = {"json", "json_output", "config", "config_path", "quiet", "help"}
MAX_VISIBLE_OPTIONS = 7
_ALL_PATHS = frozenset({".svg", ".pdf", ".eps", ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".gif", ".bmp"})


@lru_cache(maxsize=1)
def _commands() -> dict:
    return schema.build()["commands"]


class DropInput(Input):
    """Same drag-drop mechanism as the hand-built screens."""

    def _on_paste(self, event: events.Paste) -> None:
        dropped = parse_dropped_paths(event.text, extensions=_ALL_PATHS)
        if dropped:
            self.value = merge_paths_into_input(self.value, dropped)
            event.stop()
            event.prevent_default()


def _label(name: str) -> str:
    return name.replace("_", " ")


def _short(text: str, limit: int = 110) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


class AutoToolScreen(Screen):
    BINDINGS = [("escape", "go_back", "Back"), ("ctrl+r", "run", "Run")]

    def __init__(self, spec: ToolSpec) -> None:
        super().__init__()
        self.spec = spec
        self.TOOL_KEY = spec.key
        self._labels = [label for label, _ in spec.commands]
        self._command: tuple[str, ...] = spec.commands[0][1]
        self._fields: dict[str, tuple[str, dict]] = {}        # widget id -> ("arg"|"opt", schema entry)
        self._counter = 0
        self.last_summary_text = ""
        self.last_output = ""

    # ----------------------------------------------------------------------- layout
    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with VerticalScroll(id="form-pane"):
                with Vertical(id="group-what", classes="field-group"):
                    if len(self.spec.commands) > 1:
                        yield Label("What do you want to do?")
                        yield Select[str]([(label, label) for label in self._labels], id="action", allow_blank=False, value=self._labels[0])
                    yield Static("", id="command-help")
                with Vertical(id="group-fields", classes="field-group"):
                    yield Vertical(id="fields")
                with Horizontal(classes="field-row", id="action-row"):
                    yield Button("Run  [ctrl+r]", id="run-btn", variant="primary")
            with Vertical(id="results-pane"):
                with Vertical(id="group-run", classes="field-group"):
                    yield RichLog(id="run-log", markup=False, wrap=True, highlight=False)
                    yield Static("", id="run-summary")
        yield Footer()

    def on_mount(self) -> None:
        self.app.sub_title = self.spec.label
        self.query_one("#group-what", Vertical).border_title = self.spec.label
        self.query_one("#group-fields", Vertical).border_title = "Options"
        self.query_one("#group-run", Vertical).border_title = "Result"
        self._build_form()

    def action_go_back(self) -> None:
        self.app.pop_screen()

    # -------------------------------------------------------------------------- form
    def _build_form(self) -> None:
        schema_entry = _commands()[" ".join(self._command)]
        self.query_one("#command-help", Static).update(Text(_short(schema_entry["help"], 220), style="#8B93A7"))
        container = self.query_one("#fields", Vertical)
        container.remove_children()
        self._fields.clear()
        self._counter += 1
        widgets: list = []
        arguments = schema_entry["arguments"]
        options = [o for o in schema_entry["options"] if o["name"] not in HIDDEN]
        for arg in arguments:
            widgets += self._argument_widgets(arg)
        ordered = sorted(options, key=lambda o: (not o["required"],))
        visible, more = ordered[:MAX_VISIBLE_OPTIONS], ordered[MAX_VISIBLE_OPTIONS:]
        for opt in visible:
            widgets += self._option_widgets(opt)
        container.mount(*widgets)
        if more:
            extra: list = []
            for opt in more:
                extra += self._option_widgets(opt)
            container.mount(Collapsible(*extra, title=f"More options ({len(more)})", collapsed=True))

    def _wid(self, prefix: str, name: str) -> str:
        return f"{prefix}-{self._counter}-{name}"

    def _argument_widgets(self, arg: dict) -> list:
        wid = self._wid("arg", arg["name"])
        self._fields[wid] = ("arg", arg)
        is_path = arg["type"] == "path" or arg["name"] in ("inputs", "files")
        hint = _short(arg.get("help", ""))
        title = f"{_label(arg['name'])}{' *' if arg['required'] else ''}" + (" — files, folders or comma-separated; drop a file here" if is_path else "")
        widgets: list = [Label(title)]
        field = DropInput(placeholder=hint or _label(arg["name"]), id=wid) if is_path else Input(placeholder=hint or _label(arg["name"]), id=wid)
        if is_path:
            widgets.append(Horizontal(field, Button("Browse", id=self._wid("browse", arg["name"])), classes="field-row"))
        else:
            widgets.append(field)
        return widgets

    def _option_widgets(self, opt: dict) -> list:
        wid = self._wid("opt", opt["name"])
        self._fields[wid] = ("opt", opt)
        label = _label(opt["name"]) + (" *" if opt["required"] else "")
        hint = _short(opt.get("help", ""))
        if opt["type"] == "boolean":
            box = Checkbox(f"{label} — {hint}" if hint else label, id=wid)
            return [box]
        is_path = opt["type"] == "path"
        field = Input(placeholder=hint, id=wid)
        widgets: list = [Label(label)]
        if is_path:
            widgets.append(Horizontal(field, Button("Browse", id=self._wid("browse", opt["name"])), classes="field-row"))
        else:
            widgets.append(field)
        return widgets

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id != "action" or event.value in (None, Select.BLANK, Select.NULL):
            return
        command = dict(self.spec.commands)[str(event.value)]
        if command != self._command:
            self._command = command
            self._build_form()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "run-btn":
            self.action_run()
        elif bid.startswith("browse-"):
            name = bid.split("-", 2)[2]
            target = next((w for w in self._fields if w.endswith(f"-{name}")), None)
            if target is None:
                return

            def apply(path: Path | None, target: str = target) -> None:
                if path is not None:
                    field = self.query_one(f"#{target}", Input)
                    field.value = merge_paths_into_input(field.value, [path]) if self._fields[target][0] == "arg" else str(path)

            self.app.push_screen(FilePickerScreen(title=f"Choose {_label(name)}"), apply)

    # --------------------------------------------------------------------------- run
    def _argv(self) -> tuple[list[str], list[str]] | None:
        options: list[str] = []
        positional: list[str] = []
        for wid, (kind, entry) in self._fields.items():
            widget = self.query_one(f"#{wid}")
            widget.remove_class("field-error")
            if kind == "arg":
                value = widget.value.strip()
                if not value:
                    if entry["required"]:
                        widget.add_class("field-error")
                        widget.focus()
                        self._log(f"✗ {_label(entry['name'])} is required")
                        return None
                    continue
                is_path = entry["type"] == "path" or entry["name"] in ("inputs", "files")
                positional += [str(p) for p in parse_input_files_field(value)] if is_path else [value]
            else:
                flag = next(f for f in entry["flags"] if f.startswith("--") and not f.startswith("--no-"))
                if entry["type"] == "boolean":
                    if widget.value:
                        options.append(flag)
                    continue
                value = widget.value.strip()
                if not value:
                    if entry["required"]:
                        widget.add_class("field-error")
                        widget.focus()
                        self._log(f"✗ {_label(entry['name'])} is required")
                        return None
                    continue
                if entry["repeatable"]:
                    for part in [v.strip() for v in value.split(",") if v.strip()]:
                        options += [flag, part]
                else:
                    options += [flag, value]
        return options, positional

    def _log(self, message: str) -> None:
        self.query_one("#run-log", RichLog).write(Text(message))

    def action_run(self) -> None:
        built = self._argv()
        if built is None:
            return
        options, positional = built
        argv = [sys.executable, "-m", "mysuite", *self._command, *options, "--", *positional] if positional else \
               [sys.executable, "-m", "mysuite", *self._command, *options]
        self.query_one("#run-btn", Button).disabled = True
        self.query_one("#run-log", RichLog).clear()
        self.query_one("#run-summary", Static).update("running…")
        self._run_worker(argv)

    @work(thread=True, exclusive=True, group="auto-run")
    def _run_worker(self, argv: list[str]) -> None:
        env = engine._child_env()
        env["COLUMNS"] = "110"
        env["NO_COLOR"] = "1"
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, env=env, timeout=1800)
            output = (proc.stdout or "") + (proc.stderr or "")
            code = proc.returncode
        except subprocess.TimeoutExpired:
            output, code = "stopped: it took longer than 30 minutes", 1
        self.app.call_from_thread(self._on_done, output, code)

    def _on_done(self, output: str, code: int) -> None:
        self.last_output = output
        log = self.query_one("#run-log", RichLog)
        log.write(Text(output.rstrip() or "(no output)"))
        self.query_one("#run-btn", Button).disabled = False
        if code == 0:
            summary = "done"
        elif code == 4:
            summary = "a tool is missing (exit 4): see the message above"
        elif code == 3:
            summary = "refused (exit 3)"
        else:
            summary = f"failed (exit {code}): see the message above"
        self.last_summary_text = summary
        self.query_one("#run-summary", Static).update(Text(summary, style="#4ADE80" if code == 0 else "#F87171"))
