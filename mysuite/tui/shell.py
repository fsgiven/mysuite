"""One shell for the single-tool screens.

A tool screen is a form over its own CLI command: the form builds the argument list, the shell shows it as the
exact `mysuite …` command, asks the command for a dry-run plan while you edit, runs the real command, and follows
its progress. Because the screen and the CLI are the same code path, they cannot disagree — and anything the
screen does, a script or an agent can repeat from the command shown.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from rich.markup import escape
from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import (
    Button, Checkbox, Footer, Header, Input, Label, ProgressBar, RichLog, Select, Static,
)

from mysuite.pipeline import engine
from mysuite.tui import ui_state
from mysuite.tui.widgets.command_line import CommandLine
from mysuite.tui.widgets.file_target import FileTarget
from mysuite.utils import jsonout

DEBOUNCE_SECONDS = 0.35


class FormError(Exception):
    """A form value that cannot be turned into a command; `widget_id` is the field to point at."""

    def __init__(self, message: str, widget_id: str | None = None) -> None:
        super().__init__(message)
        self.widget_id = widget_id


class ChipGroup(Vertical):
    """A set of on/off chips laid out in a grid — compact replacement for a tall list of checkboxes."""

    def __init__(self, options: list[str], *, id: str, columns: int = 5, selected: list[str] | None = None) -> None:
        super().__init__(id=id, classes="chips")
        self.options = options
        self._columns = columns
        self._initial = set(selected or [])

    def compose(self) -> ComposeResult:
        for option in self.options:
            yield Checkbox(option, value=option in self._initial, id=f"{self.id}-{option}", classes="chip")

    def on_mount(self) -> None:
        self.styles.layout = "grid"
        self.styles.grid_size_columns = self._columns
        self.styles.grid_rows = "1"

    @property
    def selected(self) -> list[str]:
        return [o for o in self.options if self.query_one(f"#{self.id}-{o}", Checkbox).value]

    def select_only(self, values: list[str]) -> None:
        for option in self.options:
            self.query_one(f"#{self.id}-{option}", Checkbox).value = option in values


def field(label: str, widget, *, classes: str = "") -> Vertical:
    """A label above a widget, tight."""
    return Vertical(Label(label, classes="fl"), widget, classes=f"field {classes}".strip())


class ToolScreen(Screen):
    """Subclasses set TOOL_KEY / CLI / TITLE and implement `compose_form`, `argv`; the rest is here."""

    TOOL_KEY = ""
    CLI: tuple[str, ...] = ()
    HEADING = ""
    PLAN_FLAG: str | None = "--dry-run"       # None = this tool cannot preview its plan
    PERSIST: tuple[str, ...] = ()             # ids of Inputs / Checkboxes / Selects / ChipGroups to remember

    BINDINGS = [("escape", "go_back", "Back"), ("ctrl+r", "run", "Run")]

    def __init__(self) -> None:
        super().__init__()
        self.last_summary_text = ""
        self.last_output = ""
        self.last_report: dict[str, Any] = {}
        self._preview_timer: Timer | None = None
        self._proc: subprocess.Popen | None = None
        self._planned = 0
        self._busy = False

    # ----------------------------------------------------------------- subclass surface
    def compose_form(self) -> ComposeResult:
        raise NotImplementedError

    def argv(self) -> list[str]:
        """Arguments after the command name (no --json / --dry-run). Raise FormError for a bad value."""
        raise NotImplementedError

    def prepare(self) -> None:
        """Runs once after the form exists, before defaults and remembered values (load config, fill choice lists)."""

    def apply_defaults(self) -> None:
        """Fill the form from config defaults (called before remembered values are restored)."""

    def output_dir(self) -> Path | None:
        return None

    def global_argv(self) -> list[str]:
        """Options that go before the command name (e.g. --profile NAME)."""
        return []

    def plan_text(self, report: dict[str, Any]) -> str:
        items = report.get("items", [])
        return f"{len(items)} output(s) planned" if items else "nothing to do"

    def save_preset_hook(self) -> None:
        self.app.notify("Presets are not available for this tool yet", severity="warning")

    # ------------------------------------------------------------------------ layout
    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="tool-body"):
            with Vertical(id="form-pane"):
                with VerticalScroll(id="form"):
                    yield from self.compose_form()
                with Horizontal(id="action-row"):
                    yield Button("Run  ctrl+r", id="run-btn", variant="primary")
                    yield Button("Stop", id="stop-btn", disabled=True)
                    yield Checkbox("Preview only", id="dry-run")
                    yield Button("Reset", id="reset-btn", classes="mini")
            with Vertical(id="results-pane"):
                yield Static("", id="plan")
                yield CommandLine(id="command")
                yield ProgressBar(id="run-progress", total=100, show_eta=False)
                yield RichLog(id="run-log", markup=True, wrap=True, highlight=False)
                yield Static("", id="run-summary")
                with Horizontal(id="after-run"):
                    yield Button("Open output folder", id="open-out", classes="mini")
        yield Footer()

    def on_mount(self) -> None:
        self.app.sub_title = self.HEADING
        self.query_one("#plan", Static).border_title = "Plan"
        self.query_one("#command", CommandLine).border_title = "Command"
        self.query_one("#run-log", RichLog).border_title = "Output"
        self.query_one("#after-run").display = False
        self.prepare()
        self.apply_defaults()
        self._restore()
        self.query_one("#run-progress", ProgressBar).update(total=100, progress=0)
        self._schedule_preview()
        self._focus_first()

    def _focus_first(self) -> None:
        try:
            self.query_one(FileTarget).input.focus()
        except Exception:  # noqa: BLE001 - screens without a files box
            pass

    def action_go_back(self) -> None:
        self._kill()
        self.app.pop_screen()

    def on_unmount(self) -> None:
        self._kill()
        if self._preview_timer is not None:
            self._preview_timer.stop()

    # ------------------------------------------------------------------- remembered values
    def _persist_widgets(self):
        for wid in self.PERSIST:
            try:
                yield wid, self.query_one(f"#{wid}")
            except Exception:  # noqa: BLE001
                continue

    def _snapshot(self) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for wid, widget in self._persist_widgets():
            if isinstance(widget, Input):
                values[wid] = widget.value
            elif isinstance(widget, Checkbox):
                values[wid] = widget.value
            elif isinstance(widget, Select):
                values[wid] = None if widget.value in (None, Select.BLANK, Select.NULL) else widget.value
            elif isinstance(widget, ChipGroup):
                values[wid] = widget.selected
        values.update(self.extra_state())
        return values

    def extra_state(self) -> dict[str, Any]:
        return {}

    def restore_extra(self, values: dict[str, Any]) -> None:
        pass

    def _restore(self) -> None:
        values = ui_state.tool_values(self.TOOL_KEY)
        if not values:
            return
        for wid, widget in self._persist_widgets():
            if wid not in values:
                continue
            value = values[wid]
            try:
                if isinstance(widget, Input) and isinstance(value, str):
                    widget.value = value
                elif isinstance(widget, Checkbox) and isinstance(value, bool):
                    widget.value = value
                elif isinstance(widget, Select):
                    widget.clear() if value is None else setattr(widget, "value", value)
                elif isinstance(widget, ChipGroup) and isinstance(value, list):
                    widget.select_only([v for v in value if isinstance(v, str)])
            except Exception:  # noqa: BLE001 - a stale remembered option must never break opening the tool
                continue
        self.restore_extra(values)

    def _reset(self) -> None:
        ui_state.forget_tool(self.TOOL_KEY)
        for _, widget in self._persist_widgets():
            if isinstance(widget, Input):
                widget.value = ""
            elif isinstance(widget, Checkbox):
                widget.value = False
            elif isinstance(widget, Select):
                widget.clear()
            elif isinstance(widget, ChipGroup):
                widget.select_only([])
        self.restore_extra({})
        self.apply_defaults()
        self.app.notify("Settings reset to defaults", timeout=2)

    # ----------------------------------------------------------------------- live preview
    def on_input_changed(self, event: Input.Changed) -> None:
        self._schedule_preview()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        self._schedule_preview()

    def on_select_changed(self, event: Select.Changed) -> None:
        self._schedule_preview()

    def on_file_target_changed(self, event: FileTarget.Changed) -> None:
        self._schedule_preview()

    def _schedule_preview(self) -> None:
        if self._preview_timer is not None:
            self._preview_timer.stop()
        self._preview_timer = self.set_timer(DEBOUNCE_SECONDS, self._refresh_preview)

    def _full_argv(self, *, dry: bool) -> list[str]:
        argv = list(self.argv())
        if dry and "--dry-run" not in argv and self.PLAN_FLAG:
            argv.append(self.PLAN_FLAG)
        return argv

    def _refresh_preview(self) -> None:
        self._preview_timer = None
        command = self.query_one("#command", CommandLine)
        plan = self.query_one("#plan", Static)
        try:
            argv = [*self.global_argv(), *self.CLI, *self._display_argv()]
        except FormError as exc:
            command.show(None, "complete the form to see the command")
            plan.update(f"[dim]{escape(str(exc))}[/dim]")
            self._planned = 0
            return
        command.show(argv)
        if self.PLAN_FLAG is None or self._busy:
            return
        plan.update("[dim]planning…[/dim]")
        self._plan_worker([*argv, self.PLAN_FLAG, "--json"])

    def _display_argv(self) -> list[str]:
        argv = list(self.argv())
        if self.query_one("#dry-run", Checkbox).value and self.PLAN_FLAG and self.PLAN_FLAG not in argv:
            argv.append(self.PLAN_FLAG)
        return argv

    def _child(self, argv: list[str], *, events: bool = False) -> subprocess.Popen:
        env = engine._child_env()
        try:
            env["COLUMNS"] = str(max(60, self.query_one("#run-log").size.width - 4))
        except Exception:  # noqa: BLE001
            env["COLUMNS"] = "100"
        env["NO_COLOR"] = "1"
        if events:
            env["MYSUITE_EVENTS"] = "1"
        return subprocess.Popen(
            [sys.executable, "-m", "mysuite", *argv], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL, text=True, env=env,
        )

    @work(thread=True, exclusive=True, group="plan")
    def _plan_worker(self, argv: list[str]) -> None:
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "mysuite", *argv], capture_output=True, text=True, timeout=60,
                stdin=subprocess.DEVNULL, env={**engine._child_env(), "NO_COLOR": "1", "COLUMNS": "100"},
            )
            report = json.loads(proc.stdout) if proc.stdout.strip() else {}
        except (subprocess.TimeoutExpired, ValueError, OSError):
            report = {}
        self.app.call_from_thread(self._show_plan, report)

    def _show_plan(self, report: dict[str, Any]) -> None:
        plan = self.query_one("#plan", Static)
        if not report:
            plan.update("[dim]no preview available[/dim]")
            self._planned = 0
            return
        errors = report.get("errors") or []
        if errors and not report.get("items"):
            plan.update(f"[#FBBF24]{escape(str(errors[0]))}[/#FBBF24]")
            self._planned = 0
            return
        self._planned = len([i for i in report.get("items", []) if i.get("status") == "planned"])
        text = f"[bold]{escape(self.plan_text(report))}[/bold]"
        warnings = report.get("warnings") or []
        if warnings:
            text += "\n" + "\n".join(f"[#FBBF24]⚠ {escape(str(w))}[/#FBBF24]" for w in warnings[:3])
        plan.update(text)

    # --------------------------------------------------------------------------- running
    def write_log(self, message: str) -> None:
        self.query_one("#run-log", RichLog).write(message)

    def flash(self, widget_id: str | None, message: str) -> None:
        if widget_id:
            try:
                widget = self.query_one(f"#{widget_id}")
                widget.add_class("field-error")
                widget.focus()
            except Exception:  # noqa: BLE001
                pass
        self.query_one("#run-summary", Static).update(f"[#F87171]{escape(message)}[/#F87171]")

    def _clear_errors(self) -> None:
        for widget in self.query(".field-error"):
            widget.remove_class("field-error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "run-btn":
            self.action_run()
        elif bid == "stop-btn":
            self._kill()
        elif bid == "reset-btn":
            self._reset()
        elif bid == "open-out":
            self._open_output()

    def _open_output(self) -> None:
        folder = self.output_dir()
        if folder is None or not folder.exists():
            self.app.notify("Output folder not found", severity="warning")
            return
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(folder)])

    def _kill(self) -> None:
        proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.terminate()

    def action_run(self) -> None:
        if self._busy:
            return
        self._clear_errors()
        try:
            argv = self._display_argv()
        except FormError as exc:
            self.flash(exc.widget_id, str(exc))
            return
        ui_state.remember_tool(self.TOOL_KEY, self._snapshot())
        dry = self.query_one("#dry-run", Checkbox).value
        self._busy = True
        self.query_one("#run-btn", Button).disabled = True
        self.query_one("#stop-btn", Button).disabled = False
        self.query_one("#after-run").display = False
        self.query_one("#run-log", RichLog).clear()
        self.query_one("#run-summary", Static).update("running…")
        bar = self.query_one("#run-progress", ProgressBar)
        bar.update(total=self._planned or None, progress=0)
        self._run_worker([*self.global_argv(), *self.CLI, *argv, "--json"], dry)

    @work(thread=True, exclusive=True, group="run")
    def _run_worker(self, argv: list[str], dry: bool) -> None:
        try:
            proc = self._child(argv, events=True)
        except OSError as exc:
            self.app.call_from_thread(self._finished, {}, 1, f"could not start: {exc}", dry)
            return
        self._proc = proc
        stdout: list[str] = []
        reader = threading.Thread(target=lambda: stdout.append(proc.stdout.read()), daemon=True)
        reader.start()
        stderr_lines: list[str] = []
        assert proc.stderr is not None
        for line in proc.stderr:
            line = line.rstrip("\n")
            if line.startswith(jsonout.EVENT_PREFIX):
                try:
                    item = json.loads(line[len(jsonout.EVENT_PREFIX):])
                except ValueError:
                    continue
                self.app.call_from_thread(self._on_item, item)
                continue
            stderr_lines.append(line)
            self.app.call_from_thread(self.write_log, escape(line))
        code = proc.wait()
        reader.join(timeout=5)
        try:
            report = json.loads("".join(stdout)) if "".join(stdout).strip() else {}
        except ValueError:
            report = {}
        self.app.call_from_thread(self._finished, report, code, "\n".join(stderr_lines), dry)

    def _on_item(self, item: dict[str, Any]) -> None:
        if item.get("status") != "planned":
            self.query_one("#run-progress", ProgressBar).advance(1)

    def summary_text(self, report: dict[str, Any], code: int, dry: bool) -> str:
        items = report.get("items", [])
        written = sum(1 for i in items if i.get("status") in ("written", "ok"))
        existed = sum(1 for i in items if i.get("status") == "skipped_existing")
        failed = sum(1 for i in items if i.get("status") == "failed")
        if dry:
            return f"dry run — {len(items)} file(s) would be written, 0 written"
        text = f"done — {written} written"
        if existed:
            text += f", {existed} already existed (enable Overwrite to replace)"
        if failed:
            text += f", {failed} failed"
        return text

    def _finished(self, report: dict[str, Any], code: int, output: str, dry: bool) -> None:
        self._busy = False
        self._proc = None
        self.last_report = report
        self.last_output = output
        self.query_one("#run-btn", Button).disabled = False
        self.query_one("#stop-btn", Button).disabled = True
        bar = self.query_one("#run-progress", ProgressBar)
        if code == 0:
            bar.update(total=1, progress=1)
        if code == 0:
            summary = self.summary_text(report, code, dry)
            color = "#4ADE80"
            if dry:
                self.write_log(f"[dim]{escape(summary)}[/dim]")
            else:
                for target in self.query(FileTarget):
                    target.remember()
                if self.output_dir() is not None:
                    self.query_one("#after-run").display = True
        elif code == 4:
            summary, color = "a tool is missing (exit 4) — see the message in Output; `mysuite install` can fetch it", "#F87171"
        elif code == 3:
            summary, color = "refused by the sandbox (exit 3)", "#F87171"
        elif code < 0:
            summary, color = "stopped", "#FBBF24"
        else:
            errors = report.get("errors") or []
            summary, color = (str(errors[0]) if errors else f"failed (exit {code}) — see Output"), "#F87171"
        self.last_summary_text = summary
        self.query_one("#run-summary", Static).update(Text(summary, style=color))
        self._schedule_preview()
