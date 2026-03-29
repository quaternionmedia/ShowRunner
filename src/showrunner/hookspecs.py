"""ShowRunner hook specifications.

Defines the contract that plugins implement to participate in the ShowRunner system.
"""

import pluggy

hookspec = pluggy.HookspecMarker("showrunner")


class ShowRunnerSpec:
    """Hook specifications for ShowRunner plugins."""

    @hookspec
    def showrunner_register(self) -> dict:
        """Return plugin metadata.

        Returns a dict with keys:
            name: Human-readable plugin name (e.g. "ShowScripter")
            description: Brief description of what the plugin does
            version: Plugin version string
        """

    @hookspec
    def showrunner_startup(self, app):
        """Called when the ShowRunner application starts.

        Use this to initialize resources, open connections, etc.

        :param app: The ShowRunnerApp instance.
        """

    @hookspec
    def showrunner_shutdown(self, app):
        """Called when the ShowRunner application shuts down.

        Use this to release resources, close connections, etc.

        :param app: The ShowRunnerApp instance.
        """

    @hookspec
    def showrunner_get_routes(self):
        """Return a FastAPI APIRouter with this plugin's HTTP routes.

        :returns: A ``fastapi.APIRouter`` instance, or ``None``.
        """

    @hookspec
    def showrunner_get_commands(self) -> list:
        """Return CLI commands this plugin provides.

        Each command is a dict with keys:
            name: Command name
            description: Brief help text
            callback: Callable to invoke
        """
