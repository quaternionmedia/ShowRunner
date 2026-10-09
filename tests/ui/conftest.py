"""Fixtures for NiceGUI page tests (no browser).

Each fixture seeds a fresh database, points ``showrunner.plugins.db._db`` at it
so ``get_db()`` works, registers the plugin's page with its ``_build_page()``
and yields a NiceGUI ``User`` bound to an isolated app::

    async def test_something(scripter_ui):
        user, show_id = scripter_ui
        await user.open('/script')
        await user.should_see('Test Script')
"""

import importlib
import os
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager

import httpx
import pytest

from showrunner.database import ShowDatabase
from showrunner.models import Script, Show


@asynccontextmanager
async def plugin_simulation(setup_fn: Callable) -> AsyncGenerator:
    """``nicegui.testing.user_simulation`` with the plugin's pages registered.

    ``setup_fn`` runs after NiceGUI's route registry is reset and before the
    lifespan starts, which is when ``showrunner_startup`` calls
    ``_build_page()`` in production.
    """
    from nicegui import core, ui
    from nicegui.functions.download import download
    from nicegui.functions.navigate import Navigate
    from nicegui.functions.notify import notify
    from nicegui.testing.general import nicegui_reset_globals, prepare_simulation
    from nicegui.testing.user import User

    with nicegui_reset_globals():
        os.environ['NICEGUI_USER_SIMULATION'] = 'true'
        try:
            prepare_simulation()
            ui.run(None, storage_secret='test-secret')
            setup_fn()
            async with core.app.router.lifespan_context(core.app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(core.app), base_url='http://test'
                ) as client:
                    yield User(client)
        finally:
            os.environ.pop('NICEGUI_USER_SIMULATION', None)
            ui.navigate = Navigate()
            ui.notify = notify
            ui.download = download


@asynccontextmanager
async def seeded(tmp_path, seed: Callable) -> AsyncGenerator:
    """A fresh database seeded by ``seed(session)``, installed as the ShowDB ``_db``."""
    # import_module rather than a dotted import: nicegui_reset_globals evicts
    # showrunner from sys.modules between tests.
    db_module = importlib.import_module('showrunner.plugins.db')
    db = ShowDatabase(tmp_path / 'test.db')
    db.create_schema()
    with db.session() as s:
        result = seed(s)
    original = db_module._db
    db_module._db = db
    try:
        yield result
    finally:
        db_module._db = original
        db.close()


@pytest.fixture
async def dashboard_ui(tmp_path) -> AsyncGenerator:
    """``User`` on the dashboard (``/``) with one show."""
    dash = importlib.import_module('showrunner.plugins.dashboard')

    def seed(s):
        s.add(Show(name='Hamlet'))
        s.commit()

    async with seeded(tmp_path, seed):
        async with plugin_simulation(dash._build_page) as user:
            yield user


@pytest.fixture
async def scripter_ui(tmp_path) -> AsyncGenerator:
    """``(user, show_id)`` on ``/script`` with one show and one Fountain script."""
    scripter = importlib.import_module('showrunner.plugins.scripter')

    def seed(s):
        show = Show(name='Test Show')
        s.add(show)
        s.commit()
        s.refresh(show)
        s.add(Script(title='Test Script', format='fountain',
                     content='INT. OFFICE - DAY\n\nHELLO.\n', show_id=show.id))
        s.commit()
        return show.id

    async with seeded(tmp_path, seed) as show_id:
        async with plugin_simulation(scripter._build_page) as user:
            yield user, show_id
