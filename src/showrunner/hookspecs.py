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
    def showrunner_command(self, command_name: str, data: dict | None):
        """Receive commands issued by the ShowRunner system.

        :param command_name: Name of the command (e.g. "start_cue", "stop_cue")
        :param data: Optional dict of additional command data
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
    def showrunner_event(self, event_name: str, data: dict | None):
        """Receive events emitted by the ShowRunner system.

        :param event_name: Name of the event (e.g. "cue_started", "cue_completed")
        :param data: Optional dict of additional event data
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
    def showrunner_get_layer_types(self) -> dict | list[dict] | None:
        """Register cue layer backend types.

        Each entry is a dict with keys:
            type: Unique key stored in ``layers.type`` (e.g. "mix")
            label: Display name (e.g. "Mix")
            description: Short description shown in the layer type picker
            color: Default Quasar colour for new layers of this type

        The first registration of a type wins; duplicates are logged and ignored.
        A plugin that owns a type acts on cues whose layer has that type.
        """

    @hookspec
    def showrunner_get_layers(self) -> dict | list[dict] | None:
        """Provide layers to add to every show that does not already have them.

        Each entry is a dict with keys ``name``, ``color`` (Quasar colour) and
        ``type`` (must be registered via ``showrunner_get_layer_types``). Existing
        layers are never changed; entries with an unknown type are skipped.
        """

    @hookspec
    def showrunner_script_previews(self, cues: list[dict]) -> dict[int, list[dict]] | None:
        """Return preview chips for cues shown in the script viewer.

        ``cues`` are the viewer's cue dicts (id, number, point, name, layer,
        script_line, script_char). Return ``{cue_id: [chip, ...]}`` for the cues
        this plugin recognises, or ``None``. Each chip is a dict with keys:
            text: Short label (e.g. "1 Judge")
            color: Quasar colour for the text (default "white")
            tooltip: Optional hover text

        The viewer shows the chips under the cue's line, and again at the top of
        each following page until a later cue with chips takes over.
        """

    @hookspec
    def showrunner_get_status(self) -> dict | list[dict] | None:
        """Return status icon(s) for the global header bar.

        Each entry is a dict with keys:
            icon: Material icon name (e.g. "wifi", "mic")
            tooltip: Hover text describing the status
            color: Quasar color name (e.g. "green", "red", "grey")
        """
