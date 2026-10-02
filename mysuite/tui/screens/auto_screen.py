"""A dashboard screen generated from the CLI itself, on the same shell as the hand-built screens.

Every command already describes its arguments and options (mysuite.schema), so a tool that has no hand-built screen
gets one for free: a form with one field per option, the live command, a dry-run plan where the command has one, remembered
values per action, and the command's own output. It runs the real command, so the screen and the CLI can never disagree.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Checkbox, Collapsible, Input, Label, Select, Static

from mysuite import schema
from mysuite.tui import ui_state
from mysuite.tui.registry import ToolSpec
from mysuite.tui.shell import FormError, ToolScreen
from mysuite.tui.widgets.file_target import FileTarget

HIDDEN = {"json", "json_output", "config", "config_path", "quiet", "help", "dry_run"}   # dry_run is the shell's "Preview only"
MAX_VISIBLE_OPTIONS = 6
_BLANK = (None, Select.BLANK, Select.NULL)


@lru_cache(maxsize=1)
def _commands() -> dict:
    return schema.build()["commands"]


def _label(name: str) -> str:
    return name.replace("_", " ")


def _short(text: str, limit: int = 110) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _flag(entry: dict, *, negative: bool = False) -> str:
    longs = [f for f in entry["flags"] if f.startswith("--")]
    if negative:
        return next((f for f in longs if f.startswith("--no-") or f in ("--baseline", "--lossy")), "")
    return next((f for f in longs if not f.startswith("--no-")), longs[0])


class AutoToolScreen(ToolScreen):
    PERSIST = ()

    def __init__(self, spec: ToolSpec) -> None:
        super().__init__()
        self.spec = spec
        self.TOOL_KEY = spec.key
        self.HEADING = spec.label
        self._labels = [label for label, _ in spec.commands]
        self._command: tuple[str, ...] = spec.commands[0][1]
        self._fields: dict[str, tuple[str, dict]] = {}        # widget id -> ("arg"|"opt", schema entry)
        self._targets: dict[str, FileTarget] = {}
        self._counter = 0
        self._cmds: dict[str, dict[str, Any]] = {}            # remembered values per command: {"kit make": {"kit": "favicon"}}
        self._help = ""

    # ----------------------------------------------------------------------------- layout
    def compose_form(self) -> ComposeResult:
        if len(self.spec.commands) > 1:
            yield Label("What do you want to do?", classes="section")
            yield Select[str]([(label, label) for label, _ in self.spec.commands], id="action", allow_blank=False, value=self._labels[0])
        yield Static("", id="command-help", classes="hint")
        yield Vertical(id="fields")

    async def on_mount(self) -> None:          # Textual also runs ToolScreen.on_mount afterwards (restores remembered values)
        remembered = ui_state.tool_values(self.TOOL_KEY)
        self._cmds = {k: v for k, v in (remembered.get("cmds") or {}).items() if isinstance(v, dict)}
        label = remembered.get("action")
        if label in self._labels and len(self._labels) > 1:
            self._command = dict(self.spec.commands)[label]
            self.query_one("#action", Select).value = label
        await self._build_form()

    # -------------------------------------------------------------------------- the form
    @property
    def _key(self) -> str:
        return " ".join(self._command)

    async def _build_form(self) -> None:
        entry = _commands()[self._key]
        self._help = _short(entry["help"], 220)
        self.query_one("#command-help", Static).update(Text(self._help, style="#8B93A7"))
        container = self.query_one("#fields", Vertical)
        await container.remove_children()
        self._fields.clear()
        self._targets.clear()
        self._counter += 1
        widgets: list = []
        for arg in entry["arguments"]:
            widgets += self._argument_widgets(arg)
        options = [o for o in entry["options"] if o["name"] not in HIDDEN]
        ordered = sorted(options, key=lambda o: (not o["required"],))
        visible, more = ordered[:MAX_VISIBLE_OPTIONS], ordered[MAX_VISIBLE_OPTIONS:]
        if visible:
            widgets.append(Label("Options", classes="section"))
        for opt in visible:
            widgets += self._option_widgets(opt)
        if more:
            extra: list = []
            for opt in more:
                extra += self._option_widgets(opt)
            widgets.append(Collapsible(*extra, title=f"More options ({len(more)})", collapsed=True))
        await container.mount(*widgets)

        has_dry = any(o["name"] == "dry_run" for o in entry["options"])
        self.PLAN_FLAG = "--dry-run" if has_dry else None
        dry = self.query_one("#dry-run", Checkbox)
        dry.display = has_dry
        if not has_dry:
            dry.value = False
        self._apply_remembered()
        self._apply_accent()
        self._schedule_preview()

    def _wid(self, prefix: str, name: str) -> str:
        return f"{prefix}-{self._counter}-{name}"

    def _argument_widgets(self, arg: dict) -> list:
        wid = self._wid("arg", arg["name"])
        self._fields[wid] = ("arg", arg)
        title = _label(arg["name"]) + (" *" if arg["required"] else "")
        hint = _short(arg.get("help", ""))
        if arg["type"] == "path" or arg["name"] in ("inputs", "files"):
            target = FileTarget(
                input_id=wid, browse_id=self._wid("browse", arg["name"]), extensions=None, noun="file",
                placeholder=hint or "path, folder, or glob", folders=bool(arg["repeatable"]),
            )
            self._targets[wid] = target
            return [Label(title, classes="section"), target]
        return [Label(title, classes="section"), Input(placeholder=hint or _label(arg["name"]), id=wid)]

    def _option_widgets(self, opt: dict) -> list:
        wid = self._wid("opt", opt["name"])
        self._fields[wid] = ("opt", opt)
        label = _label(opt["name"]) + (" *" if opt["required"] else "")
        hint = _short(opt.get("help", ""))
        if opt["type"] == "boolean":
            return [Checkbox(f"{label} — {hint}" if hint else label, id=wid, value=opt.get("default") is True)]
        field = Input(placeholder=hint, id=wid)
        if opt["type"] in ("path", "file"):
            row = Horizontal(
                field,
                Button("Files…", id=self._wid("pick", opt["name"]), classes="mini"),
                Button("Folder…", id=self._wid("pickdir", opt["name"]), classes="mini"),
                classes="field-row",
            )
            return [Label(label, classes="fl"), row]
        return [Label(label, classes="fl"), field]

    # ------------------------------------------------------------ remembered values per action
    def _apply_remembered(self) -> None:
        values = self._cmds.get(self._key, {})
        for wid, (kind, entry) in self._fields.items():
            if kind != "opt" or entry["name"] not in values:
                continue
            widget = self.query_one(f"#{wid}")
            value = values[entry["name"]]
            if isinstance(widget, Checkbox) and isinstance(value, bool):
                widget.value = value
            elif isinstance(widget, Input) and isinstance(value, str):
                widget.value = value

    def _current_values(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for wid, (kind, entry) in self._fields.items():
            if kind == "opt":
                widget = self.query_one(f"#{wid}")
                out[entry["name"]] = widget.value
        return out

    def extra_state(self) -> dict[str, Any]:
        self._cmds[self._key] = self._current_values()
        action = next((label for label, cmd in self.spec.commands if cmd == self._command), self._labels[0])
        return {"action": action, "cmds": self._cmds}

    def restore_extra(self, values: dict[str, Any]) -> None:
        if not values:                                     # Reset: clear what is on screen
            self._cmds = {}
            for wid, (kind, entry) in self._fields.items():
                widget = self.query_one(f"#{wid}")
                if isinstance(widget, Checkbox):
                    widget.value = entry.get("default") is True
                else:
                    widget.value = ""

    # ----------------------------------------------------------------------------- events
    async def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id != "action" or event.value in _BLANK:
            return
        command = dict(self.spec.commands)[str(event.value)]
        if command != self._command:
            self._cmds[self._key] = self._current_values()
            self._command = command
            await self._build_form()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        from mysuite.tui.screens.file_picker import FilePickerScreen

        bid = event.button.id or ""
        for prefix, folders in (("pickdir-", True), ("pick-", False)):
            if bid.startswith(prefix):
                name = bid.split("-", 2)[2]
                target = next((w for w, (_, e) in self._fields.items() if e["name"] == name and w.startswith("opt-")), None)
                if target is None:
                    return
                event.stop()

                def apply(path: Path | None, target: str = target) -> None:
                    if path is not None:
                        self.query_one(f"#{target}", Input).value = str(path)

                self.app.push_screen(FilePickerScreen(pick_directories=folders, title=f"Choose {_label(name)}"), apply)
                return

    # ---------------------------------------------------------------------------- command
    def command_words(self) -> tuple[str, ...]:
        return self._command

    def argv(self) -> list[str]:
        positional: list[str] = []
        options: list[str] = []
        for wid, (kind, entry) in self._fields.items():
            label = _label(entry["name"])
            if kind == "arg":
                if wid in self._targets:
                    res = self._targets[wid].resolve()
                    if res.empty:
                        if entry["required"]:
                            raise FormError(f"{label} is required — paste a path, drop a file, or use Files…", wid)
                        continue
                    if res.missing or res.empty_folders:
                        bad = (res.missing or [str(p) for p in res.empty_folders])[0]
                        raise FormError(f"not found: {bad}" if res.missing else f"no files in {bad}", wid)
                    if not entry["repeatable"] and len(res.args) != 1:
                        raise FormError(f"{label} must be a single file", wid)
                    positional += res.args
                else:
                    value = self.query_one(f"#{wid}", Input).value.strip()
                    if not value:
                        if entry["required"]:
                            raise FormError(f"{label} is required", wid)
                        continue
                    positional.append(value)
                continue
            widget = self.query_one(f"#{wid}")
            if entry["type"] == "boolean":
                default = entry.get("default") is True
                if widget.value and not default:
                    options.append(_flag(entry))
                elif not widget.value and default and _flag(entry, negative=True):
                    options.append(_flag(entry, negative=True))
                continue
            value = widget.value.strip()
            if not value:
                if entry["required"]:
                    raise FormError(f"{label} is required", wid)
                continue
            if entry["type"] in ("int", "int range", "float", "float range"):
                try:
                    float(value)
                except ValueError:
                    raise FormError(f"{label} must be a number, not {value!r}", wid) from None
            parts = [v.strip() for v in value.split(",") if v.strip()] if entry["repeatable"] else [value]
            for part in parts:
                options += [_flag(entry), part]
        if any(p.startswith("-") for p in positional):
            return [*options, "--", *positional]
        return [*positional, *options]

    # ---------------------------------------------------------------------------- results
    def idle_plan_text(self) -> str:
        return self._help

    def output_dir(self) -> Path | None:
        for item in self.last_report.get("items", []):
            if isinstance(item.get("output"), str):
                return Path(item["output"]).parent
        return None

    def plan_text(self, report: dict[str, Any]) -> str:
        items = report.get("items", [])
        outs = [i for i in items if isinstance(i.get("output"), str)]
        if not outs:
            return f"{len(items)} item(s) planned" if items else "nothing to do"
        lines = [f"{len(outs)} output(s) planned"]
        lines += [f"{Path(i['input']).name if isinstance(i.get('input'), str) else '·'} → {Path(i['output']).name}" for i in outs[:4]]
        if len(outs) > 4:
            lines.append(f"… and {len(outs) - 4} more")
        return "\n".join(lines)

    def summary_text(self, report: dict[str, Any], code: int, dry: bool) -> str:
        items = report.get("items", [])
        failed = sum(1 for i in items if i.get("status") == "failed")
        if dry:
            return f"preview — {len(items)} item(s), nothing written"
        if failed:
            return f"failed (exit {code}) — {failed} of {len(items)} failed"
        text = "done"
        written = sum(1 for i in items if i.get("status") in ("written", "ok"))
        existed = sum(1 for i in items if i.get("status") == "skipped_existing")
        if written:
            text += f" — {written} written"
        if existed:
            text += f"{' —' if not written else ','} {existed} already existed (tick Replace/overwrite to redo)"
        return text

    def _finished(self, report: dict[str, Any], code: int, output: str, dry: bool) -> None:
        super()._finished(report, code, output, dry)
        if not output.strip() and report.get("items"):             # a command that only returns data: show it
            from rich.markup import escape
            import json as _json

            for item in report["items"][:50]:
                self.write_log(escape(_json.dumps(item, ensure_ascii=False)))
