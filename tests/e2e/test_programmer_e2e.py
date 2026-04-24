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
    # Wait for the page heading specifically — a cue named "Scene: Programmer"
    # also exists in the seeded data, so wait for the h5-styled div only.
    page.wait_for_selector(".text-h5.font-bold", timeout=5000)
    return page


def test_page_title(programmer_page):
    """The page renders the Programmer heading."""
    # Use CSS class selector to avoid strict-mode collision with cue names.
    assert programmer_page.locator(".text-h5.font-bold").first.is_visible()


def test_timing_panel_visible(programmer_page):
    """All three timing rows (TIME / SHOW / CUE) are visible."""
    # :text-is() does exact case-sensitive matching, avoiding collisions with
    # "ShowRunner", "Show" dropdown label, and other partial matches.
    for label in ("TIME", "SHOW", "CUE"):
        assert programmer_page.locator(f":text-is('{label}')").is_visible(), (
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
    """RESET clears any in-progress state so GO can fire from the top again.

    Shared server state means the pointer may be past the end from earlier
    tests; always reset first to put the fixture in a known baseline.
    """
    programmer_page.get_by_role("button", name="RESET").click()
    programmer_page.wait_for_selector("text=Reset to top.", timeout=3000)
    programmer_page.get_by_role("button", name="GO").click()
    programmer_page.wait_for_selector("text=GO →", timeout=3000)
