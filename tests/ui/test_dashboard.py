"""NiceGUI page-level tests for ShowDashboard (``/``).

Run with::

    uv run pytest tests/ui/test_dashboard.py -v
"""

import pytest

pytestmark = pytest.mark.ui


async def test_page_loads(dashboard_ui):
    """The dashboard root page loads and renders the heading."""
    user = dashboard_ui
    await user.open("/")
    await user.should_see("Show Control Dashboard")


async def test_app_branding_present(dashboard_ui):
    """The ShowRunner brand link appears in the header."""
    user = dashboard_ui
    await user.open("/")
    await user.should_see("ShowRunner")
