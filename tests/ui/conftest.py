"""Shared fixtures for NiceGUI page-level (no-browser) UI tests.

Each plugin fixture seeds an in-memory database, patches the module-level
``_db`` reference so ``get_db()`` works, registers the plugin's page via its
``_build_page()`` call, and yields a NiceGUI ``User`` bound to the isolated
app instance.

Usage in test files::

    async def test_something(programmer_ui):
        user, show_id, cl_id = programmer_ui
        await user.open('/programmer')
        await user.should_see('Programmer')
"""

import os
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager

import importlib

import httpx
import pytest

from showrunner.database import ShowDatabase
from showrunner.models import Cue, CueList, Script, Show


class MockApp:
    """Minimal app stand-in for ``_app_ref`` so REST route functions can
    access the database without a running ShowRunner server.
    """

    def __init__(self, db: ShowDatabase) -> None:
        self.db = db
        self.config = None


@asynccontextmanager
async def plugin_simulation(setup_fn: Callable) -> AsyncGenerator:
    """Isolated NiceGUI simulation for a single plugin page.

    Mirrors :func:`nicegui.testing.user_simulation` but injects
    ``setup_fn()`` *after* the global route registry is cleared and *before*
    the lifespan starts, so the plugin's ``@ui.page`` decorators register
    against the clean slate.  This matches how each plugin registers its page
    in production (inside ``showrunner_startup`` → ``_build_page()``).

    Args:
        setup_fn: Callable with no arguments that registers the plugin pages,
            typically ``plugin_module._build_page``.

    Yields:
        :class:`nicegui.testing.User` connected to the isolated app.
    """
    from nicegui import core, ui
    from nicegui.functions.download import download
    from nicegui.functions.navigate import Navigate
    from nicegui.functions.notify import notify
    from nicegui.testing.general import nicegui_reset_globals, prepare_simulation
    from nicegui.testing.user import User

    with nicegui_reset_globals():
        os.environ["NICEGUI_USER_SIMULATION"] = "true"
        try:
            prepare_simulation()
            ui.run(None, storage_secret="test-secret")
            setup_fn()  # register plugin @ui.page routes on the clean slate
            async with core.app.router.lifespan_context(core.app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(core.app),
                    base_url="http://test",
                ) as client:
                    yield User(client)
        finally:
            os.environ.pop("NICEGUI_USER_SIMULATION", None)
            ui.navigate = Navigate()
            ui.notify = notify
            ui.download = download


# ---------------------------------------------------------------------------
# Programmer fixture
# ---------------------------------------------------------------------------


@pytest.fixture
async def programmer_ui(tmp_path) -> AsyncGenerator:
    """NiceGUI ``User`` wired to a seeded ``/programmer`` page.

    Yields:
        ``(user, show_id, cue_list_id)`` — the simulated browser user and
        the IDs of the seeded Show and CueList.
    """
    # importlib.import_module avoids the dotted-alias bytecode that breaks
    # after nicegui_reset_globals evicts showrunner from sys.modules.
    db_module = importlib.import_module("showrunner.plugins.db")
    prog = importlib.import_module("showrunner.plugins.programmer")

    # Build a fresh DB with one show, one cue list, and three cues.
    db = ShowDatabase(tmp_path / "test.db")
    db.create_schema()
    with db.session() as s:
        show = Show(name="Test Show")
        s.add(show)
        s.commit()
        s.refresh(show)
        cl = CueList(name="Main", show_id=show.id)
        s.add(cl)
        s.commit()
        s.refresh(cl)
        for i, name in enumerate(["Intro", "Scene 1", "Scene 2"], start=1):
            s.add(Cue(number=i, name=name, cue_list_id=cl.id))
        s.commit()
        show_id = show.id
        cl_id = cl.id

    # Patch module-level singletons so page renders and REST calls hit the
    # test DB instead of the (absent) production database.
    original_db = db_module._db
    original_app_ref = prog._app_ref
    db_module._db = db
    prog._app_ref = MockApp(db=db)

    # Reset all programmer timing / pointer state for a clean baseline.
    prog._cue_pointer = 0
    prog._active_cues = []
    prog._active_cue_list_id = None
    prog._last_fire_at = None
    prog._last_fire_name = ""
    prog._show_start_at = None

    async with plugin_simulation(prog._build_page) as user:
        yield user, show_id, cl_id

    # Restore originals — next test gets a fresh import after module eviction.
    db_module._db = original_db
    prog._app_ref = original_app_ref
    db.close()


# ---------------------------------------------------------------------------
# Scripter fixture
# ---------------------------------------------------------------------------


@pytest.fixture
async def scripter_ui(tmp_path) -> AsyncGenerator:
    """NiceGUI ``User`` wired to a seeded ``/script`` page.

    Yields:
        ``(user, show_id)`` — the simulated browser user and the seeded show ID.
    """
    db_module = importlib.import_module("showrunner.plugins.db")
    scripter = importlib.import_module("showrunner.plugins.scripter")

    db = ShowDatabase(tmp_path / "test.db")
    db.create_schema()
    with db.session() as s:
        show = Show(name="Test Show")
        s.add(show)
        s.commit()
        s.refresh(show)
        s.add(
            Script(
                title="Test Script",
                format="fountain",
                content="INT. OFFICE - DAY\n\nHELLO.\n",
                show_id=show.id,
            )
        )
        s.commit()
        show_id = show.id

    original_db = db_module._db
    db_module._db = db

    async with plugin_simulation(scripter._build_page) as user:
        yield user, show_id

    db_module._db = original_db
    db.close()


# ---------------------------------------------------------------------------
# Dashboard fixture
# ---------------------------------------------------------------------------


@pytest.fixture
async def dashboard_ui(tmp_path) -> AsyncGenerator:
    """NiceGUI ``User`` wired to a seeded ``/`` page.

    Yields:
        The simulated browser user.
    """
    dash = importlib.import_module("showrunner.plugins.dashboard")
    db_module = importlib.import_module("showrunner.plugins.db")

    db = ShowDatabase(tmp_path / "test.db")
    db.create_schema()
    with db.session() as s:
        s.add(Show(name="Hamlet"))
        s.commit()

    original_db = db_module._db
    db_module._db = db

    async with plugin_simulation(dash._build_page) as user:
        yield user

    db_module._db = original_db
    db.close()
