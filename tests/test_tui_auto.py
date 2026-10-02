from __future__ import annotations

import shutil

import pytest
from PIL import Image
from textual.widgets import Button, Checkbox, Input, Select

from mysuite import schema
from mysuite.tui.app import MysuiteApp
from mysuite.tui.registry import TOOL_REGISTRY
from mysuite.tui.screens.auto_screen import HIDDEN, AutoToolScreen
from tests.helpers import SIMPLE_SVG

KEYS = [s.key for s in TOOL_REGISTRY]
AUTO = [s for s in TOOL_REGISTRY if s.commands]
need = pytest.mark.skipif(shutil.which("rsvg-convert") is None, reason="needs rsvg-convert")


async def open_tool(pilot, key):
    await pilot.pause()
    pilot.app.screen._open_tool(key)          # digit keys only reach the first nine cards
    await pilot.pause(0.3)
    return pilot.app.screen


async def finish(pilot):
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()


def test_registry_has_cards_for_the_new_tool_groups_with_distinct_colours():
    assert {"kits", "pdf", "exact", "helpers"} <= set(KEYS)
    assert len({s.accent for s in TOOL_REGISTRY}) == len(TOOL_REGISTRY)
    assert all(s.help and s.cli.startswith("mysuite ") for s in TOOL_REGISTRY)


def test_every_command_of_every_auto_card_exists_in_the_cli():
    known = schema.build()["commands"]
    for spec in AUTO:
        for label, command in spec.commands:
            assert " ".join(command) in known, (spec.key, label)


@pytest.mark.asyncio
@pytest.mark.parametrize("spec", AUTO, ids=[s.key for s in AUTO])
async def test_every_action_builds_a_form_without_machine_options(spec):
    app = MysuiteApp()
    async with app.run_test(size=(150, 60)) as pilot:
        screen = await open_tool(pilot, spec.key)
        assert isinstance(screen, AutoToolScreen)
        for label, command in spec.commands:
            if len(spec.commands) > 1:
                screen.query_one("#action", Select).value = label
                await pilot.pause(0.2)
            entry = schema.build()["commands"][" ".join(command)]
            wanted = {a["name"] for a in entry["arguments"]} | {o["name"] for o in entry["options"] if o["name"] not in HIDDEN}
            present = {entry_["name"] for _, entry_ in screen._fields.values()}
            assert present == wanted, (spec.key, label, present ^ wanted)
            assert not any(n in present for n in HIDDEN)


@need
@pytest.mark.asyncio
async def test_kits_card_makes_a_favicon_set(tmp_path):
    svg = tmp_path / "brand.svg"
    svg.write_text(SIMPLE_SVG)
    app = MysuiteApp()
    async with app.run_test(size=(150, 60)) as pilot:
        screen = await open_tool(pilot, "kits")
        screen.query_one(f"#{next(w for w, (k, e) in screen._fields.items() if e['name'] == 'inputs')}", Input).value = str(svg)
        screen.query_one(f"#{next(w for w, (k, e) in screen._fields.items() if e['name'] == 'kit')}", Input).value = "favicon"
        screen.query_one(f"#{next(w for w, (k, e) in screen._fields.items() if e['name'] == 'out')}", Input).value = str(tmp_path / "k")
        screen.action_run()
        await finish(pilot)
        assert screen.last_summary_text.startswith("done")
    assert Image.open(tmp_path / "k" / "brand" / "favicon" / "favicon-32x32.png").size == (32, 32)


@pytest.mark.asyncio
async def test_pdf_card_switches_action_and_runs_it(tmp_path):
    pdf = tmp_path / "doc.pdf"
    pages = [Image.new("RGB", (100, 100), c) for c in ("red", "blue", "green")]
    pages[0].save(pdf, format="PDF", save_all=True, append_images=pages[1:])
    app = MysuiteApp()
    async with app.run_test(size=(150, 60)) as pilot:
        screen = await open_tool(pilot, "pdf")
        screen.query_one("#action", Select).value = "Keep only some pages"
        await pilot.pause(0.3)
        ids = {e["name"]: w for w, (k, e) in screen._fields.items()}
        assert "pages" in ids and "files" in ids and "degrees" not in ids         # the form followed the action
        screen.query_one(f"#{ids['files']}", Input).value = str(pdf)
        screen.query_one(f"#{ids['pages']}", Input).value = "2-3"
        screen.action_run()
        await finish(pilot)
        assert screen.last_summary_text.startswith("done") and "doc_pages.pdf" in screen.last_output
    from pypdf import PdfReader

    assert len(PdfReader(str(tmp_path / "doc_pages.pdf")).pages) == 2


@pytest.mark.asyncio
async def test_required_fields_are_flagged_and_nothing_runs(tmp_path):
    app = MysuiteApp()
    async with app.run_test(size=(150, 60)) as pilot:
        screen = await open_tool(pilot, "kits")
        screen.action_run()
        await pilot.pause()
        first = next(w for w, (k, e) in screen._fields.items() if e["required"])
        assert screen.query_one(f"#{first}").has_class("field-error")
        assert screen.last_summary_text == "" and not screen.query_one("#run-btn", Button).disabled


@pytest.mark.asyncio
async def test_failures_and_exit_codes_are_shown(tmp_path):
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"nope")
    app = MysuiteApp()
    async with app.run_test(size=(150, 60)) as pilot:
        screen = await open_tool(pilot, "exact")
        screen.query_one("#action", Select).value = "Output at an exact size (px / cm / in)"
        await pilot.pause(0.3)
        ids = {e["name"]: w for w, (k, e) in screen._fields.items()}
        screen.query_one(f"#{ids['inputs']}", Input).value = str(bad)
        screen.query_one(f"#{ids['size']}", Input).value = "100x100"
        screen.action_run()
        await finish(pilot)
        assert screen.last_summary_text.startswith("failed (exit 1)") and "bad.png" in screen.last_output
        assert not screen.query_one("#run-btn", Button).disabled


@pytest.mark.asyncio
async def test_helpers_contrast_runs_without_files():
    app = MysuiteApp()
    async with app.run_test(size=(150, 60)) as pilot:
        screen = await open_tool(pilot, "helpers")
        screen.query_one("#action", Select).value = "Check colour contrast"
        await pilot.pause(0.3)
        ids = {e["name"]: w for w, (k, e) in screen._fields.items()}
        screen.query_one(f"#{ids['foreground']}", Input).value = "#000000"
        screen.query_one(f"#{ids['background']}", Input).value = "#ffffff"
        screen.action_run()
        await finish(pilot)
        assert screen.last_summary_text.startswith("done") and "21.0:1" in screen.last_output


@pytest.mark.asyncio
async def test_boolean_options_become_checkboxes_and_pass_their_flag(tmp_path):
    src = tmp_path / "a.png"
    Image.new("RGB", (20, 20), "red").save(src)
    app = MysuiteApp()
    async with app.run_test(size=(150, 60)) as pilot:
        screen = await open_tool(pilot, "exact")
        screen.query_one("#action", Select).value = "Output at an exact size (px / cm / in)"
        await pilot.pause(0.3)
        ids = {e["name"]: w for w, (k, e) in screen._fields.items()}
        screen.query_one(f"#{ids['inputs']}", Input).value = str(src)
        screen.query_one(f"#{ids['size']}", Input).value = "50x50"
        assert "dry_run" not in ids                                       # the shell's own "Preview only" box does this
        screen.query_one("#dry-run", Checkbox).value = True
        screen.action_run()
        await finish(pilot)
        assert "50x50" in screen.last_output and not (tmp_path / "a_print.png").exists()          # preview only: nothing written


@pytest.mark.asyncio
async def test_help_works_on_a_generated_screen():
    app = MysuiteApp()
    async with app.run_test(size=(150, 60)) as pilot:
        await open_tool(pilot, "kits")
        await pilot.press("f1")
        await pilot.pause(1.2)
        helper = app.screen
        assert type(helper).__name__ == "HelperScreen" and "Kits" in str(helper.query_one("#helper-title").render())


@pytest.mark.asyncio
async def test_generated_screens_have_plan_command_and_remembered_values(tmp_path, monkeypatch):
    import json, shlex
    from mysuite.tui import ui_state
    from mysuite.tui.widgets.command_line import CommandLine
    from tests.helpers import cli

    monkeypatch.setenv("MYSUITE_UI_STATE", str(tmp_path / "state.json"))
    monkeypatch.delenv("MYSUITE_NO_UI_STATE")
    src = tmp_path / "a.png"
    Image.new("RGB", (20, 20), "red").save(src)
    async with MysuiteApp().run_test(size=(150, 60)) as pilot:
        screen = await open_tool(pilot, "exact")
        screen.query_one("#action", Select).value = "Output at an exact size (px / cm / in)"
        await pilot.pause(0.3)
        ids = {e["name"]: w for w, (k, e) in screen._fields.items()}
        screen.query_one(f"#{ids['inputs']}", Input).value = str(src)
        screen.query_one(f"#{ids['size']}", Input).value = "50x50"
        await pilot.pause(0.6)
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()
        parts = shlex.split(screen.query_one(CommandLine).command)[1:]
        assert parts[0] == "print" and "--size" in parts
        doc = json.loads(cli(*parts, "--dry-run", "--json").stdout)           # the shown command is valid CLI
        assert doc["ok"] and "planned" in str(screen.query_one("#plan").render())
        screen.action_run()
        await finish(pilot)
    saved = ui_state.tool_values("exact")
    assert saved["action"] == "Output at an exact size (px / cm / in)" and saved["cmds"]["print"]["size"] == "50x50"
    async with MysuiteApp().run_test(size=(150, 60)) as pilot:                # comes back on the same action, same values
        screen = await open_tool(pilot, "exact")
        await pilot.pause(0.3)
        assert screen._command == ("print",)
        ids = {e["name"]: w for w, (k, e) in screen._fields.items()}
        assert screen.query_one(f"#{ids['size']}", Input).value == "50x50"
        assert screen.query_one(f"#{ids['inputs']}", Input).value == ""        # files never remembered


@pytest.mark.asyncio
async def test_a_run_that_only_skipped_existing_files_says_so(tmp_path):
    src = tmp_path / "a.png"
    Image.new("RGB", (20, 20), "red").save(src)
    (tmp_path / "a_print.png").write_bytes(b"already here")
    async with MysuiteApp().run_test(size=(150, 60)) as pilot:
        screen = await open_tool(pilot, "exact")
        screen.query_one("#action", Select).value = "Output at an exact size (px / cm / in)"
        await pilot.pause(0.3)
        ids = {e["name"]: w for w, (k, e) in screen._fields.items()}
        screen.query_one(f"#{ids['inputs']}", Input).value = str(src)
        screen.query_one(f"#{ids['size']}", Input).value = "50x50"
        screen.action_run()
        await finish(pilot)
        assert "already existed" in screen.last_summary_text and "written" not in screen.last_summary_text
