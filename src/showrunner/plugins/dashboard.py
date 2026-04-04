"""ShowDashboard plugin for Showrunner.

Provides a NiceGUI-based web dashboard mounted at the root URL.
Displays a show selector and will eventually serve as the main
show control interface.
"""

from nicegui import ui
from sqlmodel import select

import showrunner
from showrunner.database import ShowDatabase
from showrunner.models import Show


def _build_page(db: ShowDatabase) -> None:
    """Build the root dashboard page."""

    @ui.page('/')
    def index():
        with db.session() as s:
            shows = s.exec(select(Show).order_by(Show.name)).all()
            options = {show.id: str(show) for show in shows}

        ui.dark_mode(True)

        with ui.column().classes('w-full items-center mt-16'):
            ui.label('ShowRunner').classes('text-h3 font-bold')
            ui.label('Show Control Dashboard').classes('text-subtitle1 text-grey')

            with ui.card().classes('mt-8 w-96'):
                ui.label('Current Show').classes('text-h6')
                if options:
                    ui.select(
                        options=options,
                        value=next(iter(options)),
                        label='Select a show',
                    ).classes('w-full')
                else:
                    ui.label('No shows found. Create one with:').classes(
                        'text-grey mt-2'
                    )
                    ui.code('sr create My Show Name').classes('mt-1')


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
        _build_page(db)
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
