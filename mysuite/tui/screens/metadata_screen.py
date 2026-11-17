"""Metadata: strip hidden data, credit the author, declare "no AI training", protect & credit in one go, or apply a
company policy — copies, originals untouched. (`metadata randomize` is CLI-only behind --advanced: not offered here.)

A form over `mysuite metadata <mode> …`; see mysuite.tui.shell. Stable ids: `#input-files`, `#mode`, `#author`,
`#copyright`, `#group-credit` (shown for credit / protect / apply), `#group-protect`, `#key` (masked; passed to the
command as MYSUITE_MARK_KEY, never on the command line and never remembered).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
import os

from textual.widgets import Checkbox, Collapsible, Input, Label, Select, Static

from mysuite.config import load_config
from mysuite.convert._parsing import SOURCE_EXTENSIONS
from mysuite.metadata.hidden import REMOVED
from mysuite.metadata.metadata import DATA_MINING
from mysuite.tui.shell import FormError, ToolScreen, field
from mysuite.tui.widgets.file_target import FileTarget

_SOURCES = frozenset(SOURCE_EXTENSIONS)
_BLANK = (None, Select.BLANK, Select.NULL)
MODES = [
    ("Strip — remove hidden data (GPS, camera, serials)", "strip"),
    ("Credit — write author, copyright and credit line", "credit"),
    ("Declare — say 'no AI training' in the file", "declare"),
    ("Protect & credit — strip, credit, declare (and mark) in one go", "protect"),
    ("Apply policy — run a set of steps in one go", "apply"),
]
_DESCRIPTION = {
    "strip": f"Removes {REMOVED}. Keeps the colour profile. Result: <name>_stripped. PDFs: title, author, dates, XMP.",
    "credit": "Writes author, copyright, credit line, source and usage terms as IPTC Core (what picture desks read). "
              "Adds a C2PA record only if c2patool is installed (test certificate). Result: <name>_credited.",
    "declare": "Writes a machine-readable 'AI training / data mining not allowed' notice. Honoured by good-faith crawlers only. "
               "Nothing else is removed: a GPS location stays.",
    "protect": "Removes GPS, camera and serials (keeps credit and colour profile), writes your credit, declares 'no AI use', "
               "and optionally adds your invisible mark. One file, <name>_protected; nothing else is left behind.",
    "apply": "Runs the steps of a policy in order into one file (default: the selected company profile's). "
             "Steps: strip, strip-private, credit, declare, mark, protect.",
}


class MetadataScreen(ToolScreen):
    TOOL_KEY = "metadata"
    CLI = ("metadata",)
    HEADING = "Metadata"
    PERSIST = (
        "mode", "author", "copyright", "credit-line", "source", "usage-terms", "no-ai", "keep-credit", "drop-icc",
        "declare-policy", "owner", "terms-url", "apply-policy", "mark", "mark-id", "profile", "overwrite", "recursive",
    )                                  # never "key": the mark key is a secret

    def compose_form(self) -> ComposeResult:
        yield Label("Files", classes="section")
        yield FileTarget(
            input_id="input-files", browse_id="browse-input-files", extensions=_SOURCES, noun="file",
            placeholder="photo.jpg, a folder, or a glob like ~/Pictures/*.jpg",
        )
        yield Checkbox("Include subfolders", id="recursive")

        yield Label("What to do", classes="section")
        yield field("Mode", Select[str](MODES, id="mode", allow_blank=False, value="strip"))
        yield Static("", id="mode-help", classes="hint")

        with Vertical(id="group-strip"):
            yield Checkbox("Keep author, copyright and credit line (remove only private data)", id="keep-credit")
            yield Checkbox("Also remove the colour profile (colours may change)", id="drop-icc")
        with Vertical(id="group-credit"):
            with Horizontal(classes="pair"):
                yield field("Author", Input(placeholder="Jane Doe", id="author"))
                yield field("Copyright notice (optional)", Input(placeholder="© 2026 Jane Doe", id="copyright"))
        with Vertical(id="group-credit-more"):
            with Horizontal(classes="pair"):
                yield field("Credit line (optional)", Input(placeholder="Jane Doe / Acme News", id="credit-line"))
                yield field("Source (optional)", Input(placeholder="Acme Archive", id="source"))
            yield field("Usage terms (optional)", Input(placeholder="Editorial use only", id="usage-terms"))
        with Vertical(id="group-credit-extra"):
            yield Checkbox("Also record: no AI training / data mining", id="no-ai")
        with Vertical(id="group-declare"):
            yield field(
                "What is not allowed",
                Select[str]([(label, key) for key, label in DATA_MINING.items()], id="declare-policy", prompt="prohibited"),
            )
            with Horizontal(classes="pair"):
                yield field("Terms URL", Input(placeholder="https://example.com/terms", id="terms-url"))
                with Vertical(id="group-owner"):
                    yield field("Owner", Input(placeholder="Jane Doe", id="owner"))
        with Vertical(id="group-protect"):
            yield Checkbox("Also add my invisible ownership mark", id="mark")
            with Horizontal(classes="pair"):
                yield field("Mark key (secret, never saved)", Input(placeholder="or set MYSUITE_MARK_KEY", password=True, id="key"))
                yield field("Mark id (optional)", Input(placeholder="campaign or recipient", id="mark-id"))
        with Vertical(id="group-apply"):
            yield field("Steps, in order (blank = the profile's policy)", Input(placeholder="strip,credit", id="apply-policy"))

        yield Label("Company profile", classes="section")
        yield field("Profile (fills author, policy, …)", Select[str]([], id="profile", prompt="none"))
        with Collapsible(title="Safety", collapsed=True, id="adv-safety"):
            yield Checkbox("Replace files that already exist", id="overwrite")

    def prepare(self) -> None:
        self.query_one("#profile", Select).set_options([(n, n) for n in sorted(load_config().profiles)])

    def on_mount(self) -> None:          # Textual also runs ToolScreen.on_mount; no super() call needed
        self._update_mode()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "mode":
            self._update_mode()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id == "mark":
            self._update_mode()

    def _mode(self) -> str:
        value = self.query_one("#mode", Select).value
        return "strip" if value in _BLANK or value not in _DESCRIPTION else str(value)

    def _update_mode(self) -> None:
        mode = self._mode()
        self.query_one("#group-strip").display = mode == "strip"
        self.query_one("#group-credit").display = mode in ("credit", "protect", "apply")
        self.query_one("#group-credit-more").display = mode in ("credit", "protect")
        self.query_one("#group-credit-extra").display = mode == "credit"
        self.query_one("#group-declare").display = mode in ("declare", "protect")
        self.query_one("#group-owner").display = mode == "declare"
        self.query_one("#group-protect").display = mode == "protect"
        self.query_one("#group-apply").display = mode == "apply"
        self.query_one("#mode-help", Static).update(_DESCRIPTION[mode])

    def _marking(self) -> bool:
        return self._mode() == "protect" and self.query_one("#mark", Checkbox).value

    def child_env(self) -> dict[str, str]:
        key = self._text("key") if self._marking() else ""
        return {"MYSUITE_MARK_KEY": key} if key else {}

    def command_words(self) -> tuple[str, ...]:
        return ("metadata", self._mode())

    def global_argv(self) -> list[str]:
        profile = self.query_one("#profile", Select).value
        return [] if profile in _BLANK else ["--profile", str(profile)]

    def _text(self, wid: str) -> str:
        return self.query_one(f"#{wid}", Input).value.strip()

    def argv(self) -> list[str]:
        res = self.query_one(FileTarget).resolve()
        if res.empty:
            raise FormError("choose at least one file — paste a path, drop a file, or use Files… / Folder…", "input-files")
        if res.missing or res.unsupported or res.empty_folders:
            bad = (res.missing or [str(p) for p in res.unsupported] or [str(p) for p in res.empty_folders])[0]
            raise FormError(f"not usable: {bad}" if res.missing or res.unsupported else f"no files in {bad}", "input-files")
        mode = self._mode()
        out: list[str] = []

        def opt(flag: str, value: str | None) -> None:
            if value:
                out.extend([flag, value])

        has_profile = bool(self.global_argv())

        def credit_fields(*, more: bool) -> None:
            opt("--author", self._text("author"))
            opt("--copyright", self._text("copyright"))
            if more:
                opt("--credit-line", self._text("credit-line"))
                opt("--source", self._text("source"))
                opt("--usage-terms", self._text("usage-terms"))

        def declare_policy() -> None:
            policy = self.query_one("#declare-policy", Select).value
            if policy not in _BLANK:
                opt("--policy", str(policy))
            opt("--terms-url", self._text("terms-url"))

        if mode == "strip":
            if self.query_one("#keep-credit", Checkbox).value:
                out.append("--keep-credit")
            if self.query_one("#drop-icc", Checkbox).value:
                out.append("--drop-icc")
        elif mode in ("credit", "protect"):
            if not self._text("author") and not has_profile:
                raise FormError(f"enter an author name for {mode.title()} (or pick a company profile that has one)", "author")
            credit_fields(more=True)
            if mode == "credit" and self.query_one("#no-ai", Checkbox).value:
                out.append("--no-ai")
            if mode == "protect":
                declare_policy()
                if self._marking():
                    if not self._text("key") and not os.environ.get("MYSUITE_MARK_KEY"):
                        raise FormError("the mark needs your secret key (at least 4 characters)", "key")
                    if self._text("key") and len(self._text("key")) < 4:
                        raise FormError("the key must be at least 4 characters", "key")
                    out.append("--mark")
                    opt("--id", self._text("mark-id"))
        elif mode == "declare":
            declare_policy()
            opt("--owner", self._text("owner"))
        elif mode == "apply":
            if not self._text("apply-policy") and not has_profile:
                raise FormError("list the steps (for example strip,credit) or pick a company profile with a policy", "apply-policy")
            opt("--policy", self._text("apply-policy"))
            credit_fields(more=False)
        if self.query_one("#recursive", Checkbox).value:
            out.append("--recursive")
        if self.query_one("#overwrite", Checkbox).value:
            out.append("--overwrite")
        if any(a.startswith("-") for a in res.args):
            return [*out, "--", *res.args]
        return [*res.args, *out]

    def output_dir(self) -> Path | None:
        for item in self.last_report.get("items", []):
            if item.get("output"):
                return Path(item["output"]).parent
        return None

    def plan_text(self, report: dict[str, Any]) -> str:
        items = [i for i in report.get("items", []) if i.get("output")]
        if not items:
            return "nothing to do"
        lines = [f"{len(items)} file(s) — copies are written beside the originals"]
        lines += [f"{Path(i['input']).name} → {Path(i['output']).name}" for i in items[:4]]
        if len(items) > 4:
            lines.append(f"… and {len(items) - 4} more")
        return "\n".join(lines)
