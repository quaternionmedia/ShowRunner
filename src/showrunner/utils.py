from .hookspecs import ShowRunnerSpec
import pluggy
import inspect
from . import plugins
from importlib.metadata import entry_points
from .config import ShowRunnerConfig
from loguru import logger


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

    # Discover external plugins installed via setuptools entry points.
    # We load them manually (instead of pm.load_setuptools_entrypoints)
    # so that entry points pointing to a class are instantiated first —
    # pluggy requires hook methods to be bound (i.e. on an instance).
    for ep in entry_points(group="showrunner"):
        if pm.get_plugin(ep.name) is not None:
            continue
        plugin = ep.load()
        if inspect.isclass(plugin):
            plugin = plugin()
        name = getattr(plugin, "__name__", type(plugin).__name__).lower()
        if name in disabled:
            logger.info("Skipping disabled external plugin %s", name)
            continue
        pm.register(plugin, name=ep.name)

    return pm
