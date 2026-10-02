"""Metadata: strip, replace with a decoy, credit, declare "no AI training", or apply a company policy — copies, originals untouched.

A form over `mysuite metadata <mode> …`; see mysuite.tui.shell. Stable ids: `#input-files`, `#mode`, `#author`,
`#copyright`, `#generator`, `#group-credit` (shown for credit / apply).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Checkbox, Collapsible, Input, Label, Select, Static

from mysuite.config import load_config
from mysuite.convert._parsing import SOURCE_EXTENSIONS
from mysuite.metadata.metadata import DATA_MINING
from mysuite.tui.shell import FormError, ToolScreen, field
from mysuite.tui.widgets.file_target import FileTarget

_SOURCES = frozenset(SOURCE_EXTENSIONS)
_BLANK = (None, Select.BLANK, Select.NULL)
MODES = [
    ("Strip — remove all metadata", "strip"),
    ("Randomize — strip, then write a plausible decoy camera", "randomize"),
    ("Credit — embed your name and rights", "credit"),
    ("Declare — say 'no AI training' in the file", "declare"),
    ("Apply policy — run a set of steps in one go", "apply"),
]
_DESCRIPTION = {
    "strip": "Removes EXIF, GPS, camera serials, XMP and comments. Result: <name>_stripped.",
    "randomize": "Strips everything, then writes a believable decoy camera identity. Result: <name>_randomized.",
    "credit": "Embeds author and copyright (EXIF/XMP and a signed-style manifest). Result: <name>_credited.",
    "declare": "Writes a machine-readable 'AI training / data mining not allowed' notice. Honoured by good-faith crawlers only.",
    "apply": "Runs the steps of a policy in order (default: the selected company profile's). E.g. strip,credit.",
}


class MetadataScreen(ToolScreen):
    TOOL_KEY = "metadata"
    CLI = ("metadata",)
    HEADING = "Metadata"
    PERSIST = (
        "mode", "author", "copyright", "generator", "no-ai", "declare-policy", "owner", "terms-url", "apply-policy",
        "profile", "overwrite", "recursive",
    )

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

        with Vertical(id="group-credit"):
            with Horizontal(classes="pair"):
                yield field("Author", Input(placeholder="Jane Doe", id="author"))
                yield field("Copyright notice (optional)", Input(placeholder="© 2026 Jane Doe", id="copyright"))
        with Vertical(id="group-credit-extra"):
            with Horizontal(classes="pair"):
                yield field("Generator", Input(placeholder="mysuite", id="generator"))
            yield Checkbox("Also record: no AI training / data mining", id="no-ai")
        with Vertical(id="group-declare"):
            yield field(
                "What is not allowed",
                Select[str]([(label, key) for key, label in DATA_MINING.items()], id="declare-policy", prompt="prohibited"),
            )
            with Horizontal(classes="pair"):
                yield field("Owner", Input(placeholder="Jane Doe", id="owner"))
                yield field("Terms URL", Input(placeholder="https://example.com/terms", id="terms-url"))
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

    def _mode(self) -> str:
        value = self.query_one("#mode", Select).value
        return "strip" if value in _BLANK else str(value)

    def _update_mode(self) -> None:
        mode = self._mode()
        self.query_one("#group-credit").display = mode in ("credit", "apply")
        self.query_one("#group-credit-extra").display = mode == "credit"
        self.query_one("#group-declare").display = mode == "declare"
        self.query_one("#group-apply").display = mode == "apply"
        self.query_one("#mode-help", Static).update(_DESCRIPTION[mode])

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
        if mode == "credit":
            if not self._text("author") and not has_profile:
                raise FormError("enter an author name for Credit (or pick a company profile that has one)", "author")
            opt("--author", self._text("author"))
            opt("--copyright", self._text("copyright"))
            opt("--generator", self._text("generator"))
            if self.query_one("#no-ai", Checkbox).value:
                out.append("--no-ai")
        elif mode == "declare":
            policy = self.query_one("#declare-policy", Select).value
            if policy not in _BLANK:
                opt("--policy", str(policy))
            opt("--owner", self._text("owner"))
            opt("--terms-url", self._text("terms-url"))
        elif mode == "apply":
            if not self._text("apply-policy") and not has_profile:
                raise FormError("list the steps (for example strip,credit) or pick a company profile with a policy", "apply-policy")
            opt("--policy", self._text("apply-policy"))
            opt("--author", self._text("author"))
            opt("--copyright", self._text("copyright"))
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
