"""Plugins decide how a cue number is displayed."""

import pluggy

from showrunner.hookspecs import ShowRunnerSpec
from showrunner.layers import cue_number_label

hookimpl = pluggy.HookimplMarker("showrunner")


class _Mix:
    @hookimpl
    def showrunner_format_cue_number(self, number, point, layer):
        return f"{number}.{point:02d}" if layer == "Mix" and point else None


def _pm():
    pm = pluggy.PluginManager("showrunner")
    pm.add_hookspecs(ShowRunnerSpec)
    pm.register(_Mix())
    return pm


def test_default_format_without_plugins():
    assert cue_number_label("5", 0, "Sound") == "5"
    assert cue_number_label("5", 1, "Sound") == "5.1"
    assert cue_number_label(None, 0, "Sound") == ""


def test_plugin_format_wins_for_its_layer():
    pm = _pm()
    assert cue_number_label("5", 5, "Mix", pm) == "5.05"
    assert cue_number_label("5", 5, "Sound", pm) == "5.5"
