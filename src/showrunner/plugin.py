"""ShowRunner base plugin class.

Provides a ``ShowRunnerPlugin`` base class to store
the application reference and emit events.

It is not strictly necessary to subclass this for a plugin, but it provides a convenient way to track the app reference and emit events without needing to
access the plugin manager directly.

Usage::

    import showrunner
    from showrunner.plugin import ShowRunnerPlugin


    class MyPlugin(ShowRunnerPlugin):

        @showrunner.hookimpl
        def showrunner_register(self):
            return {'name': 'MyPlugin', 'description': '...', 'version': '0.1.0'}

        @showrunner.hookimpl
        def showrunner_startup(self, app):
            super().showrunner_startup(app)
            # custom startup work here
            self.emit('myplugin.started')

        @showrunner.hookimpl
        def showrunner_shutdown(self, app):
            # custom shutdown work here
            super().showrunner_shutdown(app)

Subclasses that do *not* override ``showrunner_startup`` / ``showrunner_shutdown``
get the ``_app`` tracking for free — pluggy will find and call the base class
hookimpls directly.
"""

from __future__ import annotations

import showrunner


class ShowRunnerPlugin:
    """Base class for ShowRunner plugins.

    Stores the application reference on startup and provides :meth:`emit`
    for firing ``showrunner_event`` calls without needing a direct reference
    to ``app.pm``.
    """

    def __init__(self) -> None:
        self._app = None

    @showrunner.hookimpl
    def showrunner_startup(self, app) -> None:
        """Store the app reference. Call ``super().showrunner_startup(app)`` when overriding."""
        self._app = app

    @showrunner.hookimpl
    def showrunner_shutdown(self, app) -> None:
        """Clear the app reference. Call ``super().showrunner_shutdown(app)`` when overriding."""
        self._app = None

    def emit(self, event_name: str, data: dict | None = None) -> None:
        """Fire ``showrunner_event`` on the plugin manager.

        All plugins implementing ``showrunner_event`` (e.g. ShowLogger) will
        receive the event.  Safe to call before startup — does nothing if the
        app reference is not yet set.

        :param event_name: Event identifier, e.g. ``'qlab.cue_fired'``.
        :param data: Optional dict of event payload.
        """
        if self._app is not None:
            self._app.pm.hook.showrunner_event(event_name=event_name, data=data)
