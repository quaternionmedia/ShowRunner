"""NiceGUI page-level tests for ShowProgrammer (``/programmer``).

These tests run without a browser.  They use NiceGUI's ``User`` simulation
to open the page, inspect rendered elements, and drive button interactions.
All I/O that would normally hit a live server (OSC, HTTP cue dispatch) is
absent here — the module has no OSC targets configured and no HTTP cue notes
in the seeded data, so ``_fire_cue`` does nothing but set timing globals and
write a CueLog entry.

Run with::

    uv run pytest tests/ui/test_programmer.py -v
"""

import asyncio

import pytest

pytestmark = pytest.mark.ui


# ---------------------------------------------------------------------------
# Page structure
# ---------------------------------------------------------------------------


async def test_page_loads(programmer_ui):
    """Opening /programmer renders the page heading."""
    user, _show_id, _cl_id = programmer_ui
    await user.open("/programmer")
    await user.should_see("Programmer")


async def test_timing_panel_labels_present(programmer_ui):
    """The three timing rows (TIME / SHOW / CUE) are visible on load."""
    user, _show_id, _cl_id = programmer_ui
    await user.open("/programmer")
    await user.should_see("TIME")
    await user.should_see("SHOW")
    await user.should_see("CUE")


async def test_go_and_reset_buttons_present(programmer_ui):
    """GO and RESET buttons are rendered."""
    from nicegui import ui

    user, _show_id, _cl_id = programmer_ui
    await user.open("/programmer")
    await user.should_see(kind=ui.button, content="GO")
    await user.should_see(kind=ui.button, content="RESET")


# ---------------------------------------------------------------------------
# Cue stack
# ---------------------------------------------------------------------------


async def test_cue_stack_shows_seeded_cues(programmer_ui):
    """The three seeded cues appear in the cue stack."""
    user, _show_id, _cl_id = programmer_ui
    await user.open("/programmer")
    await user.should_see("Intro")
    await user.should_see("Scene 1")
    await user.should_see("Scene 2")


# ---------------------------------------------------------------------------
# GO interaction
# ---------------------------------------------------------------------------


async def test_go_fires_first_cue(programmer_ui):
    """Clicking GO updates the feedback label with the fired cue."""
    from nicegui import ui

    user, _show_id, _cl_id = programmer_ui
    await user.open("/programmer")

    user.find(kind=ui.button, content="GO").click()
    # _on_go is async and scheduled as a background task; retried by should_see.
    await user.should_see("GO →")
    await user.should_see("Intro")


async def test_go_advances_pointer(programmer_ui):
    """Firing GO twice fires two distinct cues."""
    from nicegui import ui

    user, _show_id, _cl_id = programmer_ui
    await user.open("/programmer")

    user.find(kind=ui.button, content="GO").click()
    await user.should_see("Intro")

    user.find(kind=ui.button, content="GO").click()
    await user.should_see("Scene 1")


async def test_go_feedback_turns_green(programmer_ui):
    """After a successful GO the feedback label carries the green class."""
    from nicegui import ui

    user, _show_id, _cl_id = programmer_ui
    await user.open("/programmer")
    user.find(kind=ui.button, content="GO").click()
    await user.should_see("GO →")

    with user:
        # Locate the feedback label — it's the only label with "GO →" text.
        labels = [
            el for el in user.current_layout.descendants()
            if hasattr(el, "_text") and "GO →" in (el._text or "")
        ]
        assert labels, "feedback label not found"
        fb = labels[0]
        assert "green" in " ".join(fb._classes), (
            f"expected green class, got {fb._classes}"
        )


# ---------------------------------------------------------------------------
# RESET interaction
# ---------------------------------------------------------------------------


async def test_reset_clears_feedback(programmer_ui):
    """Pressing RESET after GO shows the reset message."""
    from nicegui import ui

    user, _show_id, _cl_id = programmer_ui
    await user.open("/programmer")

    user.find(kind=ui.button, content="GO").click()
    await user.should_see("GO →")

    user.find(kind=ui.button, content="RESET").click()
    await user.should_see("Reset to top.")


async def test_reset_resets_pointer(programmer_ui):
    """After reset, GO fires the first cue again."""
    from nicegui import ui

    user, _show_id, _cl_id = programmer_ui
    await user.open("/programmer")

    user.find(kind=ui.button, content="GO").click()
    await user.should_see("Intro")

    user.find(kind=ui.button, content="RESET").click()
    await user.should_see("Reset to top.")

    user.find(kind=ui.button, content="GO").click()
    # Pointer is back at 0 — fires Intro again.
    await user.should_see("Intro")


# ---------------------------------------------------------------------------
# Past-end error
# ---------------------------------------------------------------------------


async def test_go_past_end_shows_error(programmer_ui):
    """Firing GO beyond the last cue surfaces an error in the feedback label."""
    from nicegui import ui

    user, _show_id, _cl_id = programmer_ui
    await user.open("/programmer")

    # Fire all three seeded cues.
    for _ in range(3):
        user.find(kind=ui.button, content="GO").click()
        await asyncio.sleep(0.15)  # allow each async handler to complete

    # One more GO should hit the "Past end" error branch.
    user.find(kind=ui.button, content="GO").click()
    await user.should_see("Past end of cue list")
