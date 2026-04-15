"""Built-in ShowRunner plugins."""

from .scripter import ShowScripterPlugin
from .printer import ShowPrinterPlugin
from .designer import ShowDesignerPlugin
from .programmer import ShowProgrammerPlugin
from .mixer import ShowMixerPlugin
from .lighter import ShowLighterPlugin
from .stage_manager import ShowManagerPlugin
from .stopper import ShowStopperPlugin
from .prompter import ShowPrompterPlugin
from .comms import ShowCommsPlugin
from .cmd import ShowCmdPlugin
from .recorder import ShowRecorderPlugin
from .voicer import ShowVoicerPlugin
from .db import ShowDBPlugin
from .admin import ShowAdminPlugin
from .dashboard import ShowDashboardPlugin


def get_builtin_plugins() -> list[type]:
    """Return all built-in plugin classes."""
    return [
        ShowScripterPlugin,
        ShowPrinterPlugin,
        ShowDesignerPlugin,
        ShowProgrammerPlugin,
        ShowMixerPlugin,
        ShowLighterPlugin,
        ShowManagerPlugin,
        ShowStopperPlugin,
        ShowPrompterPlugin,
        ShowCommsPlugin,
        ShowCmdPlugin,
        ShowRecorderPlugin,
        ShowVoicerPlugin,
        ShowDBPlugin,
        ShowAdminPlugin,
        ShowDashboardPlugin,
    ]
