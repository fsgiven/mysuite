"""The shared single-tool shell (Export first): file targeting, remembered settings, the live command, run parity."""
from __future__ import annotations

import json
import shlex
import shutil

import pytest
from textual.widgets import Checkbox, Input, Select, Static

from mysuite.tui import ui_state
from mysuite.tui.app import MysuiteApp
from mysuite.tui.shell import ChipGroup
from mysuite.tui.widgets.command_line import CommandLine
from mysuite.tui.widgets.file_target import FileTarget, PathSuggester, describe, resolve_targets
from tests.helpers import SIMPLE_SVG, cli

need_tools = pytest.mark.skipif(any(shutil.which(t) is None for t in ("rsvg-convert", "gs", "magick")), reason="needs tools")
SVG = frozenset({".svg"})


async def open_export(pilot):
    await pilot.pause()
    pilot.app.screen._open_tool("export")
    await pilot.pause()
    await pilot.pause()
    return pilot.app.screen


# ------------------------------------------------------------------------------ file targeting
def test_resolve_files_folders_globs_and_problems(tmp_path):
    (tmp_path / "a.svg").write_text(SIMPLE_SVG)
    (tmp_path / "b.svg").write_text(SIMPLE_SVG)
    (tmp_path / "notes.txt").write_text("x")
    (tmp_path / ".hidden.svg").write_text(SIMPLE_SVG)
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "c.svg").write_text(SIMPLE_SVG)

    folder = resolve_targets(str(tmp_path), SVG)
    assert [p.name for p in folder.files] == ["a.svg", "b.svg"] and folder.args == [str(tmp_path)]
    assert [p.name for p in resolve_targets(str(tmp_path), SVG, recursive=True).files] == ["a.svg", "b.svg", "c.svg"]

    globbed = resolve_targets(str(tmp_path / "*.svg"), SVG)
    assert [p.name for p in globbed.files] == ["a.svg", "b.svg"]
    assert globbed.args == [str(tmp_path / "a.svg"), str(tmp_path / "b.svg")]      # the command gets real files

    mixed = resolve_targets(f"{tmp_path / 'a.svg'}, {tmp_path / 'nope.svg'}, {tmp_path / 'notes.txt'}", SVG)
    assert [p.name for p in mixed.files] == ["a.svg"]
    assert mixed.missing == [str(tmp_path / "nope.svg")] and [p.name for p in mixed.unsupported] == ["notes.txt"]
    assert not mixed.ok

    assert resolve_targets("", SVG).empty
    assert resolve_targets(str(sub.parent / "empty-dir-that-is-missing"), SVG).missing
    anything = resolve_targets(str(tmp_path / "notes.txt"), None)
    assert [p.name for p in anything.files] == ["notes.txt"]


def test_a_path_with_a_comma_in_its_name_is_still_one_file(tmp_path):
    odd = tmp_path / "logo, final.svg"
    odd.write_text(SIMPLE_SVG)
    assert resolve_targets(str(odd), SVG).files == [odd]


def test_status_text_says_what_was_found(tmp_path):
    (tmp_path / "a.svg").write_text(SIMPLE_SVG)
    text, level = describe(resolve_targets(str(tmp_path / "a.svg"), SVG), "SVG")
    assert level == "ok" and "1 SVG" in text and "a.svg" in text
    text, level = describe(resolve_targets(str(tmp_path / "gone.svg"), SVG), "SVG")
    assert level == "error" and "not found" in text
    assert describe(resolve_targets("", SVG), "SVG")[1] == "hint"
    text, level = describe(resolve_targets(f"{tmp_path / 'a.svg'},{tmp_path / 'gone.svg'}", SVG), "SVG")
    assert level == "warn" and "1 SVG" in text and "not found" in text


async def test_path_suggester_completes_the_last_path(tmp_path):
    (tmp_path / "brand-kit").mkdir()
    (tmp_path / "brand.svg").write_text(SIMPLE_SVG)
    s = PathSuggester()
    assert await s.get_suggestion(f"{tmp_path}/br") == f"{tmp_path}/brand-kit/"
    assert await s.get_suggestion(f"/does/not/exist/{tmp_path.name}") is None
    assert await s.get_suggestion("logo") is None                                  # not path-like: no guessing
    first = f"{tmp_path}/brand.svg"
    assert (await s.get_suggestion(f"{first}, {tmp_path}/brand.")) == f"{first}, {tmp_path}/brand.svg"


async def test_file_target_shows_status_live(tmp_path):
    svg = tmp_path / "logo.svg"
    svg.write_text(SIMPLE_SVG)
    async with MysuiteApp().run_test() as pilot:
        sc = await open_export(pilot)
        target = sc.query_one(FileTarget)
        sc.query_one("#input-svg", Input).value = str(svg)
        await pilot.pause()
        assert "1 SVG" in str(sc.query_one("#input-svg-status", Static).render())
        sc.query_one("#input-svg", Input).value = str(tmp_path / "nope.svg")
        await pilot.pause()
        assert "not found" in str(sc.query_one("#input-svg-status", Static).render())
        assert not target.resolution.ok


# --------------------------------------------------------------------------------- remembered state
def test_ui_state_roundtrip_and_switches(tmp_path, monkeypatch):
    monkeypatch.setenv("MYSUITE_UI_STATE", str(tmp_path / "state.json"))
    monkeypatch.delenv("MYSUITE_NO_UI_STATE")
    assert ui_state.tool_values("export") == {}
    ui_state.remember_tool("export", {"sizes": "64", "overwrite": True})
    ui_state.remember_tool("convert", {"x": 1})
    assert ui_state.tool_values("export") == {"sizes": "64", "overwrite": True}
    ui_state.forget_tool("export")
    assert ui_state.tool_values("export") == {} and ui_state.tool_values("convert") == {"x": 1}

    real = tmp_path / "real.svg"
    real.write_text("x")
    ui_state.add_recent([real, tmp_path / "gone.svg"])
    ui_state.add_recent([real])
    assert ui_state.recent_paths() == [str(real)]                                  # de-duplicated; vanished files dropped

    (tmp_path / "state.json").write_text("{not json")                              # corrupt file = no memory, no crash
    assert ui_state.tool_values("export") == {}

    monkeypatch.setenv("MYSUITE_NO_UI_STATE", "1")
    ui_state.remember_tool("export", {"a": 1})
    assert not (tmp_path / "state.json").read_text().count('"a"') and ui_state.tool_values("export") == {}


@need_tools
async def test_last_settings_come_back_and_reset_clears_them(tmp_path, monkeypatch):
    monkeypatch.setenv("MYSUITE_UI_STATE", str(tmp_path / "state.json"))
    monkeypatch.delenv("MYSUITE_NO_UI_STATE")
    svg = tmp_path / "logo.svg"
    svg.write_text(SIMPLE_SVG)
    async with MysuiteApp().run_test() as pilot:
        sc = await open_export(pilot)
        sc.query_one("#input-svg", Input).value = str(svg)
        sc.query_one("#sizes", Input).value = "48,96"
        sc.query_one("#out-dir", Input).value = str(tmp_path / "out")
        sc.query_one("#formats", ChipGroup).select_only(["png", "webp"])
        sc.query_one("#overwrite", Checkbox).value = True
        from mysuite.tui.widgets.recolor_row import RecolorRow
        row = sc.query(RecolorRow).first()
        row.from_input.value, row.to_input.value = "#dd0000", "#00aa00"
        sc.action_run()
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()
    saved = json.loads((tmp_path / "state.json").read_text())
    assert saved["tools"]["export"]["sizes"] == "48,96" and "input-svg" not in saved["tools"]["export"]
    assert ui_state.recent_paths() == [str(svg)]                                   # the input is offered under Recent

    async with MysuiteApp().run_test() as pilot:
        sc = await open_export(pilot)
        assert sc.query_one("#sizes", Input).value == "48,96"
        assert sc.query_one("#formats", ChipGroup).selected == ["png", "webp"]
        assert sc.query_one("#overwrite", Checkbox).value is True
        assert sc.query_one("#input-svg", Input).value == ""                       # files are never pre-filled
        from mysuite.tui.widgets.recolor_row import RecolorRow
        await pilot.pause()
        row = sc.query(RecolorRow).first()
        assert (row.from_input.value, row.to_input.value) == ("#dd0000", "#00aa00")
        sc.query_one("#reset-btn").press()
        await pilot.pause()
        assert sc.query_one("#sizes", Input).value == "" and sc.query_one("#overwrite", Checkbox).value is False
    assert ui_state.tool_values("export") == {}


# ----------------------------------------------------------------------- the command is the CLI
@need_tools
async def test_the_shown_command_is_exactly_what_the_cli_accepts(tmp_path):
    svg = tmp_path / "my logo.svg"
    svg.write_text(SIMPLE_SVG)
    async with MysuiteApp().run_test() as pilot:
        sc = await open_export(pilot)
        sc.query_one("#input-svg", Input).value = str(svg)
        sc.query_one("#sizes", Input).value = "32,64"
        sc.query_one("#out-dir", Input).value = str(tmp_path / "out")
        sc.query_one("#formats", ChipGroup).select_only(["png", "pdf"])
        sc.query_one("#profiles", ChipGroup).select_only(["rgb", "cmyk"])
        sc.query_one("#cmyk-mode", Select).value = "clean"
        sc.query_one("#variants", ChipGroup).select_only(["default", "mono-white"])
        sc.query_one("#date-stamp", Checkbox).value = False
        sc.query_one("#dry-run", Checkbox).value = True
        await pilot.pause()
        await pilot.pause(0.5)
        command = sc.query_one(CommandLine).command
    assert command.startswith("mysuite export ")
    parts = shlex.split(command)[1:]                                               # what you would paste in a shell
    assert "--cmyk-mode" in parts and parts[parts.index("--variants") + 1] == "default,mono-white" and "--dry-run" in parts
    result = cli(*parts, "--json")
    assert result.exit_code == 0, result.output
    doc = json.loads(result.stdout)
    assert doc["ok"] and {i["variant"] for i in doc["items"]} == {"", "mono-white"} and not (tmp_path / "out").exists()


@need_tools
async def test_defaults_are_left_to_the_cli(tmp_path):
    """A flag only appears when you changed something, so config, presets and profiles keep their say."""
    svg = tmp_path / "logo.svg"
    svg.write_text(SIMPLE_SVG)
    async with MysuiteApp().run_test() as pilot:
        sc = await open_export(pilot)
        sc.query_one("#input-svg", Input).value = str(svg)
        assert sc.argv() == [str(svg)]
        sc.query_one("#quality", Input).value = "70"
        assert sc.argv() == [str(svg), "--quality", "70"]


@need_tools
async def test_plan_and_progress_follow_the_real_run(tmp_path):
    svg = tmp_path / "logo.svg"
    svg.write_text(SIMPLE_SVG)
    async with MysuiteApp().run_test() as pilot:
        sc = await open_export(pilot)
        sc.query_one("#input-svg", Input).value = str(svg)
        sc.query_one("#sizes", Input).value = "32,64,128"
        sc.query_one("#out-dir", Input).value = str(tmp_path / "out")
        sc.query_one("#formats", ChipGroup).select_only(["png"])
        await pilot.pause(0.4)
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()
        assert "3 file(s)" in str(sc.query_one("#plan", Static).render())
        assert sc._planned == 3
        sc.action_run()
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()
        assert sc.query_one("#run-progress").progress == sc.query_one("#run-progress").total
        assert len(list((tmp_path / "out").rglob("*.png"))) == 3
        assert sc.query_one("#after-run").display is True                           # "Open output folder" appears


async def test_bad_numbers_point_at_their_field(tmp_path):
    svg = tmp_path / "logo.svg"
    svg.write_text(SIMPLE_SVG)
    async with MysuiteApp().run_test() as pilot:
        sc = await open_export(pilot)
        sc.query_one("#input-svg", Input).value = str(svg)
        sc.query_one("#quality", Input).value = "250"
        await pilot.pause()
        sc.action_run()
        await pilot.pause()
        assert "field-error" in sc.query_one("#quality", Input).classes
        assert "0-100" in sc.last_summary_text or "0-100" in str(sc.query_one("#run-summary", Static).render())


async def test_name_needs_exactly_one_file(tmp_path):
    for n in ("a", "b"):
        (tmp_path / f"{n}.svg").write_text(SIMPLE_SVG)
    async with MysuiteApp().run_test() as pilot:
        sc = await open_export(pilot)
        sc.query_one("#input-svg", Input).value = str(tmp_path)
        sc.query_one("#name", Input).value = "x"
        await pilot.pause()
        sc.action_run()
        await pilot.pause()
        assert "field-error" in sc.query_one("#name", Input).classes


def test_events_are_off_unless_asked(tmp_path, monkeypatch):
    svg = tmp_path / "logo.svg"
    svg.write_text(SIMPLE_SVG)
    off = cli("export", svg, "--sizes", "16", "--formats", "png", "--out", tmp_path / "o1", "--json")
    assert "@@mysuite" not in (off.stderr or "")
    monkeypatch.setenv("MYSUITE_EVENTS", "1")
    on = cli("export", svg, "--sizes", "16", "--formats", "png", "--out", tmp_path / "o2", "--json")
    assert on.exit_code == 0
    line = next(l for l in (on.stderr or "").splitlines() if l.startswith("@@mysuite "))
    assert json.loads(line[len("@@mysuite "):])["status"] == "written"


# ----------------------------------------------------------------------------------------- Convert
async def open_tool(pilot, key):
    await pilot.pause()
    pilot.app.screen._open_tool(key)
    await pilot.pause()
    await pilot.pause()
    return pilot.app.screen


@need_tools
async def test_convert_screen_several_targets_and_the_shown_command(tmp_path):
    import subprocess
    photo = tmp_path / "my photo.png"
    subprocess.run(["magick", "-size", "40x30", "gradient:red-blue", "-type", "TrueColor", f"PNG24:{photo}"], check=True)
    async with MysuiteApp().run_test() as pilot:
        sc = await open_tool(pilot, "convert")
        sc.query_one("#input-files", Input).value = str(photo)
        sc.query_one("#target-format", ChipGroup).select_only(["webp", "jpeg"])
        sc.query_one("#quality", Input).value = "80"
        await pilot.pause(0.5)
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()
        assert "2 conversion(s)" in str(sc.query_one("#plan", Static).render())
        command = sc.query_one(CommandLine).command
        parts = shlex.split(command)[1:]
        doc = json.loads(cli(*parts, "--dry-run", "--json").stdout)
        assert [i["format"] for i in doc["items"]] == ["jpeg", "webp"]
        sc.action_run()
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()
        assert (tmp_path / "my photo.webp").exists() and (tmp_path / "my photo.jpg").exists()
        assert "2 written" in sc.last_summary_text
        assert sc.output_dir() == tmp_path


async def test_convert_needs_a_target_and_a_real_file(tmp_path):
    async with MysuiteApp().run_test() as pilot:
        sc = await open_tool(pilot, "convert")
        sc.action_run()
        await pilot.pause()
        assert "field-error" in sc.query_one("#input-files", Input).classes
        (tmp_path / "a.png").write_bytes(b"x")
        sc.query_one("#input-files", Input).value = str(tmp_path / "a.png")
        sc.query_one("#target-format", ChipGroup).select_only([])
        await pilot.pause()
        sc.action_run()
        await pilot.pause()
        assert "at least one format" in sc.last_summary_text or "at least one format" in str(sc.query_one("#run-summary", Static).render())


# ------------------------------------------------------------------------- Watermark and Cutout
@need_tools
async def test_watermark_command_matches_the_cli_and_validates_numbers(tmp_path):
    import subprocess
    photo = tmp_path / "photo.png"
    logo = tmp_path / "logo.png"
    subprocess.run(["magick", "-size", "120x80", "gradient:red-blue", "-type", "TrueColor", f"PNG24:{photo}"], check=True)
    subprocess.run(["magick", "-size", "30x30", "xc:white", str(logo)], check=True)
    async with MysuiteApp().run_test() as pilot:
        sc = await open_tool(pilot, "watermark")
        sc.query_one("#input-files", Input).value = str(photo)
        sc.query_one("#logo", Input).value = str(logo)
        sc.query_one("#position", Select).value = "top-left"
        sc.query_one("#opacity", Input).value = "50"
        await pilot.pause(0.5)
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()
        assert "1 watermarked" in str(sc.query_one("#plan", Static).render())
        parts = shlex.split(sc.query_one(CommandLine).command)[1:]
        assert parts[:2] == ["watermark", str(photo)] and "top-left" in parts and "--logo" in parts
        doc = json.loads(cli(*parts, "--dry-run", "--json").stdout)
        assert doc["ok"] and doc["items"][0]["status"] == "planned"
        sc.query_one("#opacity", Input).value = "150"
        await pilot.pause()
        sc.action_run()
        await pilot.pause()
        assert "field-error" in sc.query_one("#opacity", Input).classes
        sc.query_one("#opacity", Input).value = "50"
        await pilot.pause()
        sc.action_run()
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()
        assert (tmp_path / "photo_watermarked.png").exists() and "1 written" in sc.last_summary_text


async def test_watermark_logo_must_be_one_image(tmp_path):
    (tmp_path / "p.png").write_bytes(b"x")
    (tmp_path / "a.png").write_bytes(b"x")
    (tmp_path / "b.png").write_bytes(b"x")
    async with MysuiteApp().run_test() as pilot:
        sc = await open_tool(pilot, "watermark")
        sc.query_one("#input-files", Input).value = str(tmp_path / "p.png")
        sc.query_one("#logo", Input).value = f"{tmp_path / 'a.png'},{tmp_path / 'b.png'}"
        await pilot.pause()
        sc.action_run()
        await pilot.pause()
        assert "field-error" in sc.query_one("#logo", Input).classes


async def test_cutout_form_builds_the_command(tmp_path):
    photo = tmp_path / "me.jpg"
    photo.write_bytes(b"x")
    async with MysuiteApp().run_test() as pilot:
        sc = await open_tool(pilot, "cutout")
        sc.query_one("#input-files", Input).value = str(photo)
        sc.query_one("#overwrite", Checkbox).value = True
        assert sc.argv() == [str(photo), "--overwrite"]


# --------------------------------------------------------------------------------------- Metadata
async def test_metadata_modes_pick_the_subcommand_and_show_their_fields(tmp_path):
    photo = tmp_path / "me.jpg"
    photo.write_bytes(b"x")
    async with MysuiteApp().run_test() as pilot:
        sc = await open_tool(pilot, "metadata")
        sc.query_one("#input-files", Input).value = str(photo)
        mode = sc.query_one("#mode", Select)
        for value, words, groups in (
            ("strip", ("metadata", "strip"), set()),
            ("credit", ("metadata", "credit"), {"group-credit", "group-credit-extra"}),
            ("declare", ("metadata", "declare"), {"group-declare"}),
            ("apply", ("metadata", "apply"), {"group-credit", "group-apply"}),
        ):
            mode.value = value
            await pilot.pause()
            assert sc.command_words() == words
            shown = {g for g in ("group-credit", "group-credit-extra", "group-declare", "group-apply") if sc.query_one(f"#{g}").display}
            assert shown == groups, (value, shown)
        mode.value = "credit"
        await pilot.pause()
        sc.action_run()
        await pilot.pause()
        assert "field-error" in sc.query_one("#author", Input).classes          # credit needs a name (or a profile)
        sc.query_one("#author", Input).value = "Jane Doe"
        sc.query_one("#no-ai", Checkbox).value = True
        await pilot.pause()
        assert sc.argv() == [str(photo), "--author", "Jane Doe", "--no-ai"]


@need_tools
async def test_metadata_credit_command_matches_the_cli(tmp_path):
    import subprocess
    if shutil.which("exiftool") is None:
        pytest.skip("needs exiftool")
    photo = tmp_path / "me.jpg"
    subprocess.run(["magick", "-size", "40x30", "xc:#3388ff", str(photo)], check=True)
    async with MysuiteApp().run_test() as pilot:
        sc = await open_tool(pilot, "metadata")
        sc.query_one("#input-files", Input).value = str(photo)
        sc.query_one("#mode", Select).value = "declare"
        sc.query_one("#owner", Input).value = "Jane Doe"
        await pilot.pause(0.5)
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()
        parts = shlex.split(sc.query_one(CommandLine).command)[1:]
        assert parts[:3] == ["metadata", "declare", str(photo)]
        doc = json.loads(cli(*parts, "--dry-run", "--json").stdout)
        assert doc["ok"] and doc["items"][0]["output"].endswith(".jpg")


# ---------------------------------------------------------------------------------------- Compress
async def test_compress_command_follows_the_codec_and_matches_the_cli(tmp_path):
    photo = tmp_path / "photo.png"
    photo.write_bytes(b"x")
    async with MysuiteApp().run_test() as pilot:
        sc = await open_tool(pilot, "compress")
        sc.query_one("#input-files", Input).value = str(photo)
        sc.query_one("#codec", Select).value = "webp"
        sc.query_one("#quality", Input).value = "70"
        sc.query_one("#lossless", Checkbox).value = True
        sc.query_one("#method", Input).value = "3"
        await pilot.pause()
        assert sc.argv() == [str(photo), "--codec", "webp", "--quality", "70", "--method", "3", "--lossless"]
        # values of other codecs' hidden groups never leak into the command
        sc.query_one("#speed", Input).value = "9"
        assert "--speed" not in sc.argv()
        sc.query_one("#codec", Select).value = "mozjpeg"
        await pilot.pause()
        assert sc.argv() == [str(photo), "--codec", "mozjpeg", "--quality", "70", "--subsample", "4:2:0", "--progressive"]
        sc.query_one("#progressive", Checkbox).value = False
        sc.query_one("#sharpen-amount", Input).value = "abc"
        await pilot.pause()
        sc.action_run()
        await pilot.pause()
        assert "field-error" in sc.query_one("#sharpen-amount", Input).classes


@need_tools
async def test_compress_dry_run_command_is_valid_cli(tmp_path):
    import subprocess
    photo = tmp_path / "photo.png"
    subprocess.run(["magick", "-size", "40x30", "xc:#3388ff", str(photo)], check=True)
    async with MysuiteApp().run_test() as pilot:
        sc = await open_tool(pilot, "compress")
        sc.query_one("#input-files", Input).value = str(photo)
        sc.query_one("#preset", Select).value = "modern-web-avif"
        await pilot.pause(0.5)
        parts = shlex.split(sc.query_one(CommandLine).command)[1:]
        assert "--codec" in parts and parts[parts.index("--codec") + 1] == "avif" and "--speed" in parts
        result = cli(*parts, "--dry-run", "--json")
        assert result.exit_code in (0, 4), result.output               # 4 = avifenc not installed; the flags themselves parsed
        if result.exit_code == 0:
            assert json.loads(result.stdout)["items"][0]["status"] == "planned"


# ----------------------------------------------------------------------------------------- Enhance
async def test_enhance_command_is_the_preset_plus_what_you_changed(tmp_path):
    import numpy as np
    from PIL import Image
    photo = tmp_path / "photo.png"
    Image.fromarray(np.random.default_rng(0).integers(0, 255, (30, 40, 3), dtype=np.uint8)).save(photo)
    async with MysuiteApp().run_test() as pilot:
        sc = await open_tool(pilot, "enhance")
        sc.query_one("#input-files", Input).value = str(photo)
        assert sc.argv() == [str(photo)]                                         # nothing changed, nothing to say
        sc.query_one("#preset", Select).value = "old-photo"
        await pilot.pause()
        assert sc.argv() == [str(photo), "--preset", "old-photo"]                # the preset's own values are not repeated
        sc.query_one("#scale", Input).value = "4"
        sc.query_one("#restore-scratches", Checkbox).value = False
        sc.query_one("#backend", Select).value = "classical"
        await pilot.pause()
        assert sc.argv() == [str(photo), "--preset", "old-photo", "--scale", "4", "--no-restore-scratches", "--backend", "classical"]
        parts = ["enhance", "run", *sc.argv()]
        doc = json.loads(cli(*parts, "--dry-run", "--json").stdout)
        assert doc["ok"] and doc["items"][0]["output"].endswith("photo_enhanced.png")
        sc.query_one("#scale", Input).value = "99"
        await pilot.pause()
        sc.action_run()
        await pilot.pause()
        assert "field-error" in sc.query_one("#scale", Input).classes
