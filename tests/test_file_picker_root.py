from __future__ import annotations

from pathlib import Path

import pytest
from textual.widgets import Button, DirectoryTree, Input, Static

from mysuite.tui.app import MysuiteApp
from mysuite.tui.registry import TOOL_REGISTRY
from mysuite.tui.screens.file_picker import FilePickerScreen

_BROWSE_BUTTONS = [
    ("convert", "browse-input-files"),
    ("cutout", "browse-input-files"),
    ("metadata", "browse-input-files"),
    ("watermark", "browse-input-files"),
    ("watermark", "browse-logo"),
    ("compress", "browse-input-files"),
    ("enhance", "browse-input-files"),
    ("export", "browse-input-svg"),
    ("export", "browse-out-dir"),
]


async def _open_picker(pilot, tool_key: str, button_id: str) -> FilePickerScreen:
    index = next(i for i, spec in enumerate(TOOL_REGISTRY, start=1) if spec.key == tool_key)
    await pilot.pause()
    await pilot.press(str(index))
    await pilot.pause()
    pilot.app.screen.query_one(f"#{button_id}", Button).press()
    await pilot.pause()
    assert isinstance(pilot.app.screen, FilePickerScreen)
    return pilot.app.screen


def _tree_path(picker: FilePickerScreen) -> Path:
    return Path(picker.query_one(DirectoryTree).path)


@pytest.mark.parametrize("tool_key,button_id", _BROWSE_BUTTONS)
async def test_every_browse_button_opens_at_home_not_the_project_folder(tool_key, button_id):
    async with MysuiteApp().run_test() as pilot:
        picker = await _open_picker(pilot, tool_key, button_id)
        assert _tree_path(picker) == Path.home()


async def test_jump_buttons_and_parent_reach_anywhere_on_disk():
    async with MysuiteApp().run_test() as pilot:
        picker = await _open_picker(pilot, "convert", "browse-input-files")

        picker.query_one("#jump-root", Button).press()
        await pilot.pause()
        assert _tree_path(picker) == Path("/")

        picker.query_one("#jump-home", Button).press()
        await pilot.pause()
        assert _tree_path(picker) == Path.home()

        picker.query_one("#jump-parent", Button).press()
        await pilot.pause()
        assert _tree_path(picker) == Path.home().parent


async def test_typed_folder_path_jumps_there(tmp_path):
    async with MysuiteApp().run_test() as pilot:
        picker = await _open_picker(pilot, "convert", "browse-input-files")
        field = picker.query_one("#picker-path", Input)
        field.value = str(tmp_path)
        await field.action_submit()
        await pilot.pause()
        assert _tree_path(picker) == tmp_path


async def test_typed_file_path_selects_it_and_fills_the_field(tmp_path):
    photo = tmp_path / "photo, final.png"
    photo.write_bytes(b"\x89PNG")
    async with MysuiteApp().run_test() as pilot:
        picker = await _open_picker(pilot, "convert", "browse-input-files")
        field = picker.query_one("#picker-path", Input)
        field.value = str(photo)
        await field.action_submit()
        await pilot.pause()
        assert not isinstance(pilot.app.screen, FilePickerScreen)
        assert pilot.app.screen.query_one("#input-files", Input).value == str(photo)


async def test_bad_typed_path_shows_an_error_and_stays_open(tmp_path):
    async with MysuiteApp().run_test() as pilot:
        picker = await _open_picker(pilot, "convert", "browse-input-files")
        field = picker.query_one("#picker-path", Input)
        field.value = str(tmp_path / "nope.png")
        await field.action_submit()
        await pilot.pause()
        assert isinstance(pilot.app.screen, FilePickerScreen)
        assert "not found" in str(picker.query_one("#picker-current", Static).render())


async def test_picker_remembers_last_folder_for_the_same_picker(tmp_path):
    photo = tmp_path / "a.png"
    photo.write_bytes(b"\x89PNG")
    async with MysuiteApp().run_test() as pilot:
        picker = await _open_picker(pilot, "convert", "browse-input-files")
        field = picker.query_one("#picker-path", Input)
        field.value = str(photo)
        await field.action_submit()
        await pilot.pause()

        reopened = await _open_picker_from_current(pilot, "browse-input-files")
        assert _tree_path(reopened) == tmp_path


async def _open_picker_from_current(pilot, button_id: str) -> FilePickerScreen:
    pilot.app.screen.query_one(f"#{button_id}", Button).press()
    await pilot.pause()
    assert isinstance(pilot.app.screen, FilePickerScreen)
    return pilot.app.screen


async def test_hidden_files_are_not_listed(tmp_path):
    (tmp_path / ".secret").write_text("x")
    (tmp_path / "visible.png").write_bytes(b"\x89PNG")
    async with MysuiteApp().run_test() as pilot:
        picker = await _open_picker(pilot, "convert", "browse-input-files")
        picker._go(tmp_path)
        await pilot.pause()
        await pilot.pause()
        names = {Path(n.data.path).name for n in picker.query_one(DirectoryTree).root.children if n.data}
        assert "visible.png" in names and ".secret" not in names
