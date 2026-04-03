"""ShowRunner - A collection of tools for live performances."""

import pluggy

# hookimpl must be defined before importing ShowRunner (which transitively
# imports plugins that do ``import showrunner; showrunner.hookimpl``).
hookimpl = pluggy.HookimplMarker("showrunner")
"""Marker to be used in plugin hook implementations."""

from .app import ShowRunner  # noqa: E402

__all__ = ["hookimpl", "ShowRunner"]
