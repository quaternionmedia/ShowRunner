"""NiceGUI page-level tests for ShowScripter (``/script``).

Run with::

    uv run pytest tests/ui/test_scripter.py -v
"""

import pytest

pytestmark = pytest.mark.ui


async def test_page_loads(scripter_ui):
    """/script renders without error."""
    user, _show_id = scripter_ui
    await user.open("/script")
    # The page header contains the app branding.
    await user.should_see("ShowRunner")


async def test_layer_buttons_present(scripter_ui):
    """All five layer filter buttons are visible."""
    user, _show_id = scripter_ui
    await user.open("/script")
    for layer in ("Lights", "Sound", "Video", "Audio", "Stage"):
        await user.should_see(layer)


async def test_show_selector_present(scripter_ui):
    """The show dropdown is rendered."""
    from nicegui import ui

    user, _show_id = scripter_ui
    await user.open("/script")
    await user.should_see(kind=ui.select)


async def test_script_content_rendered(scripter_ui):
    """The seeded Fountain script title appears once selected."""
    user, _show_id = scripter_ui
    await user.open("/script")
    # The script title "Test Script" appears in the script selector dropdown.
    await user.should_see("Test Script")
