from __future__ import annotations

import pytest

from mysuite.tui.app import MysuiteApp


@pytest.mark.asyncio
async def test_welcome_shows_then_any_key_opens_the_overview():
    app = MysuiteApp(show_welcome=True)
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        assert type(app.screen).__name__ == "WelcomeScreen"
        await pilot.press("x")
        await pilot.pause()
        assert type(app.screen).__name__ == "HomeScreen"


@pytest.mark.asyncio
async def test_default_app_still_opens_on_the_overview():
    app = MysuiteApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert type(app.screen).__name__ == "HomeScreen"
