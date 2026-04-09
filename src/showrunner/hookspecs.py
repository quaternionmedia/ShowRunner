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
    def showrunner_command(self, command_name: str, **kwargs):
        """Receive commands issued by the ShowRunner system.

        :param command_name: Name of the command (e.g. "start_cue", "stop_cue")
        :param kwargs: Additional command data
        """

    @hookspec
    def showrunner_get_commands(self) -> list:
        """Return CLI commands this plugin provides.

        Each command is a dict with keys:
            name: Command name
            description: Brief help text
            callback: Callable to invoke
        """

    @hookspec
    def showrunner_query(self, query_name: str, **kwargs):
        """Receive queries from the ShowRunner system.

        :param query_name: Name of the query (e.g. "get_cue_status")
        :param kwargs: Additional query data
        :returns: Response data for the query
        """

    @hookspec
    def showrunner_event(self, event_name: str, **kwargs):
        """Receive events emitted by the ShowRunner system.

        :param event_name: Name of the event (e.g. "cue_started", "cue_completed")
        :param kwargs: Additional event data
        """

    @hookspec
    def showrunner_subscribe(self, event_name: str):
        """Subscribe to events emitted by the ShowRunner system.

        :param event_name: Name of the event to subscribe to (e.g. "cue_started")
        """

    @hookspec
    def showrunner_publish(self, event_name: str, **kwargs):
        """Publish an event to the ShowRunner system.

        :param event_name: Name of the event to publish (e.g. "cue_started")
        :param kwargs: Additional event data
        """

    @hookspec
    def showrunner_config_changed(self, config, previous_config):
        """Called when the configuration file is modified and successfully reloaded.

        Plugins can implement this hook to react to runtime config changes
        without a server restart.

        :param config: The new ``ShowRunnerConfig`` instance.
        :param previous_config: The previous ``ShowRunnerConfig`` instance.
        """

    @hookspec
    def showrunner_get_nav(self) -> dict | list[dict] | None:
        """Return navigation entries for the global header menu.

        Each entry is a dict with keys:
            label: Display text (e.g. "Scripts")
            path: URL path (e.g. "/script")
            icon: Optional Material icon name (e.g. "description")
            order: Sort order (default 50, lower = higher in menu)
        """

    @hookspec
    def showrunner_get_status(self) -> dict | list[dict] | None:
        """Return status icon(s) for the global header bar.

        Each entry is a dict with keys:
            icon: Material icon name (e.g. "wifi", "mic")
            tooltip: Hover text describing the status
            color: Quasar color name (e.g. "green", "red", "grey")
        """
