"""ShowLogger - Structured event logging via loguru.

Configuration in show.toml::

    [plugins.showlogger]
    level = "INFO"         # stdout log level (default: "INFO")
    format = "..."         # loguru format string (default: colourised timestamp)
    file = "show.log"      # path to log file (omit to disable file logging)
    file_level = "DEBUG"   # file sink log level (default: "DEBUG")
    rotation = "10 MB"     # file rotation threshold (default: "10 MB")
    retention = "1 week"   # how long to keep rotated files (default: "1 week")
"""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

import showrunner
from showrunner.plugin import ShowRunnerPlugin

_DEFAULT_FORMAT = (
    '{time:YYYY-MM-DD HH:mm:ss.SSS} | '
    '<level>{level: <8}</level> | '
    '<cyan>{name}</cyan> | '
    '<yellow>{message}</yellow>'
)


class ShowLoggerPlugin(ShowRunnerPlugin):
    """Log all ShowRunner plugin events to stdout (and optionally a file).

    Hooks implemented:
        - ``showrunner_startup`` / ``showrunner_shutdown``: lifecycle events
        - ``showrunner_event``: every emitted event
        - ``showrunner_command``: every dispatched command
        - ``showrunner_config_changed``: live configuration reload
    """

    def __init__(self) -> None:
        super().__init__()
        self._sink_ids: list[int] = []

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    @showrunner.hookimpl
    def showrunner_register(self) -> dict:
        logger.trace('showrunner_register called')
        return {
            'name': 'ShowLogger',
            'description': 'Structured event logging for all ShowRunner plugin events',
            'version': '0.1.0',
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @showrunner.hookimpl
    def showrunner_startup(self, app) -> None:
        logger.trace('showrunner_startup called')
        super().showrunner_startup(app)
        cfg = app.config.plugins.settings.get('showlogger', {})
        self._configure(cfg)
        logger.info('ShowLogger started')

    @showrunner.hookimpl
    def showrunner_shutdown(self, app) -> None:
        logger.trace('showrunner_shutdown called')
        logger.info('ShowLogger shutting down')
        self._remove_sinks()
        super().showrunner_shutdown(app)

    # ------------------------------------------------------------------
    # Event / command hooks
    # ------------------------------------------------------------------

    @showrunner.hookimpl
    def showrunner_event(self, event_name: str, data: dict | None) -> None:
        logger.info('event | {} | {}', event_name, data)

    @showrunner.hookimpl
    def showrunner_command(self, command_name: str, data: dict | None) -> None:
        logger.info('command | {} | {}', command_name, data)

    # showrunner_query
    @showrunner.hookimpl
    def showrunner_query(self, query_name: str, **kwargs):
        logger.trace('query | {} | {}', query_name, kwargs)

    # ------------------------------------------------------------------
    # Live config reload
    # ------------------------------------------------------------------

    @showrunner.hookimpl
    def showrunner_config_changed(self, config, previous_config) -> None:
        logger.trace('showrunner_config_changed called')
        cfg = config.plugins.settings.get('showlogger', {})
        self._configure(cfg)
        logger.info('ShowLogger reconfigured')

    # ------------------------------------------------------------------
    # Unused required hooks
    # ------------------------------------------------------------------

    @showrunner.hookimpl
    def showrunner_get_routes(self):
        logger.trace('showrunner_get_routes called')
        return None

    @showrunner.hookimpl
    def showrunner_get_commands(self) -> list:
        logger.trace('showrunner_get_commands called')
        return []

    @showrunner.hookimpl
    def showrunner_get_nav(self):
        logger.trace('showrunner_get_nav called')
        return None

    @showrunner.hookimpl
    def showrunner_get_status(self):
        logger.trace('showrunner_get_status called')
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _configure(self, cfg: dict) -> None:
        """Apply *cfg* to loguru, replacing any previously registered sinks.

        Always adds a stdout sink.  Adds a rotating file sink when ``file``
        is present in *cfg*.
        """
        self._remove_sinks()

        # Remove loguru's built-in default stderr handler so we fully own
        # the output.  The id=0 handler may already have been removed by a
        # previous call; ignore the ValueError if so.
        try:
            logger.remove(0)
        except ValueError:
            pass

        level = cfg.get('level', 'INFO').upper()
        fmt = cfg.get('format', _DEFAULT_FORMAT)

        # Stdout sink (always active)
        sid = logger.add(sys.stdout, level=level, format=fmt, colorize=True)
        self._sink_ids.append(sid)

        # Optional file sink
        file_path: str | None = cfg.get('file')
        if file_path:
            file_level = cfg.get('file_level', 'DEBUG').upper()
            rotation = cfg.get('rotation', '10 MB')
            retention = cfg.get('retention', '1 week')
            sid = logger.add(
                Path(file_path),
                level=file_level,
                rotation=rotation,
                retention=retention,
                encoding='utf-8',
                format=cfg.get('file_format', fmt),
            )
            self._sink_ids.append(sid)
            logger.debug('ShowLogger file sink: {}', file_path)

    def _remove_sinks(self) -> None:
        """Remove all sinks this plugin registered."""
        for sid in self._sink_ids:
            try:
                logger.remove(sid)
            except ValueError:
                pass
        self._sink_ids.clear()
