"""ShowRunner core application.

Manages the plugin lifecycle and wires plugins into the FastAPI application.
"""

import pluggy
from fastapi import FastAPI

from .hookspecs import ShowRunnerSpec
from . import plugins


def get_plugin_manager() -> pluggy.PluginManager:
    """Create and configure a PluginManager with built-in plugins loaded."""
    pm = pluggy.PluginManager("showrunner")
    pm.add_hookspecs(ShowRunnerSpec)

    # Register all built-in plugins
    for plugin_class in plugins.get_builtin_plugins():
        pm.register(plugin_class())

    # Discover external plugins installed via setuptools entry points
    pm.load_setuptools_entrypoints("showrunner")

    return pm


class ShowRunnerApp:
    """Main ShowRunner application.

    Holds the plugin manager and the FastAPI app instance.
    Provides lifecycle management for all registered plugins.
    """

    def __init__(self) -> None:
        self.pm = get_plugin_manager()
        self.api = FastAPI(title="ShowRunner", version="0.1.0")
        self._mount_routes()

    def _mount_routes(self) -> None:
        """Collect APIRouters from all plugins and include them in the FastAPI app."""
        routers = self.pm.hook.showrunner_get_routes()
        for router in routers:
            if router is not None:
                self.api.include_router(router)

    def startup(self) -> None:
        """Invoke the startup hook on all plugins."""
        self.pm.hook.showrunner_startup(app=self)

    def shutdown(self) -> None:
        """Invoke the shutdown hook on all plugins."""
        self.pm.hook.showrunner_shutdown(app=self)

    def list_plugins(self) -> list[dict]:
        """Return metadata from all registered plugins."""
        return self.pm.hook.showrunner_register()

    def list_commands(self) -> list[dict]:
        """Collect CLI commands from all plugins."""
        results = self.pm.hook.showrunner_get_commands()
        # Flatten the list-of-lists into a single list
        return [cmd for group in results if group for cmd in group]
