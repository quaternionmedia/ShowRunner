"""Built-in ShowRunner plugins."""

from .scripter import ShowScripterPlugin
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


def get_builtin_plugins() -> list[type]:
    """Return all built-in plugin classes."""
    return [
        ShowScripterPlugin,
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
    ]
