from __future__ import annotations

import pytest
from textual.widgets import Button, DirectoryTree

from mysuite.tui.app import MysuiteApp
from mysuite.tui.registry import TOOL_REGISTRY
from mysuite.tui.screens.file_picker import FilePickerScreen


async def _open_screen_and_browse(pilot, tool_key: str, browse_button_id: str = "browse-input-files"):
    index = next(i for i, spec in enumerate(TOOL_REGISTRY, start=1) if spec.key == tool_key)
    await pilot.pause()
    await pilot.press(str(index))
    await pilot.pause()
    screen = pilot.app.screen
    screen.query_one(f"#{browse_button_id}", Button).press()
    await pilot.pause()
    return pilot.app.screen


@pytest.mark.parametrize(
    "tool_key,browse_button_id",
    [
        ("convert", "browse-input-files"),
        ("cutout", "browse-input-files"),
        ("metadata", "browse-input-files"),
        ("watermark", "browse-input-files"),
        ("watermark", "browse-logo"),
        ("compress", "browse-input-files"),
        ("export", "browse-input-svg"),
        ("export", "browse-out-dir"),
    ],
)
async def test_browse_button_opens_picker_rooted_at_filesystem_root(tool_key, browse_button_id):
    app = MysuiteApp()
    async with app.run_test() as pilot:
        picker = await _open_screen_and_browse(pilot, tool_key, browse_button_id)
        assert isinstance(picker, FilePickerScreen), (tool_key, browse_button_id, type(picker))
        tree = picker.query_one(DirectoryTree)
        assert str(tree.path) == "/", (
            f"{tool_key}/{browse_button_id} picker is not rooted at the filesystem root — "
            f"got {tree.path!r}"
        )
