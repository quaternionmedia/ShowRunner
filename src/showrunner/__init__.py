"""ShowRunner - A collection of tools for live performances."""

import pluggy

hookimpl = pluggy.HookimplMarker("showrunner")
"""Marker to be imported and used in plugins (and for own implementations)."""

from .app import ShowRunner

__all__ = ["hookimpl", "ShowRunner"]
