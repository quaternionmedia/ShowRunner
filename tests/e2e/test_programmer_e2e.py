"""Playwright end-to-end tests for the ShowProgrammer page.

These tests open a real Chromium browser against a live ShowRunner server.
They are deliberately narrow — they verify the critical UI flows that cannot
be tested by the no-browser UI tests (real DOM rendering, keyboard shortcuts,
visual state transitions).

Prerequisites: see ``tests/e2e/conftest.py``.

Run with::

    uv run pytest tests/e2e/test_programmer_e2e.py -v
"""

import pytest

pytestmark = pytest.mark.e2e


@pytest.fixture
def programmer_page(page, live_server):
    """Navigate to /programmer and return the Playwright ``Page``."""
    page.goto(f"{live_server}/programmer")
    page.wait_for_selector("text=Programmer", timeout=5000)
    return page


def test_page_title(programmer_page):
    """The page renders the Programmer heading."""
    assert programmer_page.locator("text=Programmer").is_visible()


def test_timing_panel_visible(programmer_page):
    """All three timing rows (TIME / SHOW / CUE) are visible."""
    for label in ("TIME", "SHOW", "CUE"):
        assert programmer_page.locator(f"text={label}").is_visible(), (
            f"timing label '{label}' not visible"
        )


def test_go_button_visible(programmer_page):
    """The GO button is rendered and enabled on page load."""
    go = programmer_page.get_by_role("button", name="GO")
    assert go.is_visible()
    assert go.is_enabled()


def test_go_click_fires_cue(programmer_page):
    """Clicking GO updates the feedback label."""
    programmer_page.get_by_role("button", name="GO").click()
    programmer_page.wait_for_selector("text=GO →", timeout=3000)


def test_space_shortcut_fires_go(programmer_page):
    """Pressing Space triggers the GO action (same as clicking GO)."""
    programmer_page.keyboard.press("Space")
    programmer_page.wait_for_selector("text=GO →", timeout=3000)


def test_clock_starts_after_go(programmer_page):
    """The CUE timer leaves its idle state after a GO."""
    programmer_page.get_by_role("button", name="GO").click()
    # After GO, the CUE elapsed label should no longer read '--:--.--'.
    programmer_page.wait_for_function(
        "() => !document.body.innerText.includes('--:--.--')",
        timeout=2000,
    )


def test_reset_restores_idle_state(programmer_page):
    """RESET after GO clears feedback and shows 'Reset to top.'."""
    programmer_page.get_by_role("button", name="GO").click()
    programmer_page.wait_for_selector("text=GO →", timeout=3000)
    programmer_page.get_by_role("button", name="RESET").click()
    programmer_page.wait_for_selector("text=Reset to top.", timeout=3000)
