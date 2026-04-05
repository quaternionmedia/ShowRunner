"""ShowDashboard plugin for Showrunner.

Provides a NiceGUI-based web dashboard mounted at the root URL.
Displays a show selector and will eventually serve as the main
show control interface.
"""

from nicegui import ui

import showrunner
from showrunner.plugins.db import get_db
from showrunner.models import Show
from showrunner.ui import header


def _build_page() -> None:
    """Build the root dashboard page."""

    @ui.page('/')
    def index():
        ui.dark_mode(True)
        header()

        with ui.column().classes('w-full items-center mt-8'):
            ui.label('Show Control Dashboard').classes('text-h4 font-bold')


class ShowDashboardPlugin:
    """NiceGUI web dashboard for show control."""

    @showrunner.hookimpl
    def showrunner_register(self):
        return {
            'name': 'ShowDashboard',
            'description': 'NiceGUI web dashboard for show control',
            'version': '0.1.0',
        }

    @showrunner.hookimpl(trylast=True)
    def showrunner_startup(self, app):
        db = getattr(app, 'db', None)
        if db is None:
            return

        from showrunner.ui import set_plugin_manager

        set_plugin_manager(app.pm)

        _build_page()
        config = getattr(app, 'config', None)
        secret = config.server.storage_secret if config else 'showrunner'
        ui.run_with(app.api, storage_secret=secret)

    @showrunner.hookimpl
    def showrunner_shutdown(self, app):
        pass

    @showrunner.hookimpl
    def showrunner_get_routes(self):
        return None

    @showrunner.hookimpl
    def showrunner_get_commands(self):
        return []

    @showrunner.hookimpl
    def showrunner_get_nav(self):
        return {'label': 'Dashboard', 'path': '/', 'icon': 'dashboard', 'order': 0}

    @showrunner.hookimpl
    def showrunner_get_status(self):
        return None
