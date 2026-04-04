"""ShowRunner core application.

Manages the plugin lifecycle and wires plugins into the FastAPI application.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pluggy
from fastapi import FastAPI

from . import plugins
from .config import ConfigWatcher, ShowRunnerConfig, load_config
from .hookspecs import ShowRunnerSpec

logger = logging.getLogger(__name__)


def get_plugin_manager(
    config: ShowRunnerConfig | None = None,
) -> pluggy.PluginManager:
    """Create and configure a PluginManager with built-in plugins loaded.

    Plugins whose name (case-insensitive) appears in
    ``config.plugins.disabled`` are skipped.
    """
    pm = pluggy.PluginManager("showrunner")
    pm.add_hookspecs(ShowRunnerSpec)

    disabled = set()
    if config is not None:
        disabled = {n.lower() for n in config.plugins.disabled}

    # Register all built-in plugins
    for plugin_class in plugins.get_builtin_plugins():
        name = plugin_class.__name__.lower()
        if name in disabled:
            logger.info("Skipping disabled plugin %s", plugin_class.__name__)
            continue
        pm.register(plugin_class())

    # Discover external plugins installed via setuptools entry points
    pm.load_setuptools_entrypoints("showrunner")

    return pm


class ShowRunner:
    """Main ShowRunner application.

    Holds the plugin manager and the FastAPI app instance.
    Provides lifecycle management for all registered plugins.
    """

    def __init__(self, config_path: Path | None = None) -> None:
        self.config = load_config(config_path)
        self._config_path = config_path or self.config._source_path
        self.pm = get_plugin_manager(self.config)
        self._config_watcher: ConfigWatcher | None = None
        self.api = FastAPI(title="ShowRunner", version="0.1.0")
        self._mount_routes()

    def _mount_routes(self) -> None:
        """Collect APIRouters from all plugins and include them in the FastAPI app."""
        routers = self.pm.hook.showrunner_get_routes()
        for router in routers:
            if router is not None:
                self.api.include_router(router)

    def startup(self) -> None:
        """Invoke the startup hook on all plugins and start the config watcher."""
        self.pm.hook.showrunner_startup(app=self)

        if self._config_path is not None:
            self._config_watcher = ConfigWatcher(self._config_path, self)
            self._config_watcher.start()
            logger.info("Watching %s for changes", self._config_path)
            print(f"  Config:     {self._config_path} (watching for changes)")

    def shutdown(self) -> None:
        """Invoke the shutdown hook on all plugins and stop the config watcher."""
        if self._config_watcher is not None:
            self._config_watcher.stop()
            self._config_watcher = None
        self.pm.hook.showrunner_shutdown(app=self)

    def list_plugins(self) -> list[dict]:
        """Return metadata from all registered plugins."""
        return self.pm.hook.showrunner_register()

    def list_commands(self) -> list[dict]:
        """Collect CLI commands from all plugins."""
        results = self.pm.hook.showrunner_get_commands()
        # Flatten the list-of-lists into a single list
        return [cmd for group in results if group for cmd in group]
