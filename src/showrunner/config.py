"""ShowRunner configuration.

Loads application settings from a TOML file (``show.toml``) with automatic
file discovery, Pydantic validation, and optional live-reload via file watching.
"""

from __future__ import annotations

import asyncio
import logging
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config models
# ---------------------------------------------------------------------------


class DatabaseConfig(BaseModel):
    """Database connection settings."""

    path: str = "show.db"
    echo: bool = False


class ServerConfig(BaseModel):
    """HTTP server settings."""

    host: str = "0.0.0.0"
    port: int = 8000
    storage_secret: str = "showrunner"


class LoggingConfig(BaseModel):
    """Logging settings."""

    level: str = "INFO"


class PathsConfig(BaseModel):
    """Default directory paths."""

    scripts: str = "./scripts"
    exports: str = "./exports"


class PluginsConfig(BaseModel):
    """Plugin management settings.

    ``disabled`` lists plugin names (case-insensitive) to skip during loading.
    ``settings`` holds per-plugin config dicts keyed by lowercase plugin name,
    populated from ``[plugins.<name>]`` sections in the TOML file.
    """

    disabled: list[str] = Field(default_factory=list)
    settings: dict[str, dict[str, Any]] = Field(default_factory=dict)


class ShowRunnerConfig(BaseModel):
    """Root configuration model for ShowRunner."""

    current_show: int | None = None
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    plugins: PluginsConfig = Field(default_factory=PluginsConfig)

    # Not part of TOML — tracks where the config was loaded from.
    _source_path: Path | None = None


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

CONFIG_FILENAME = "show.toml"


def find_config(start: Path | None = None) -> Path | None:
    """Locate the configuration file.

    Search order:
    1. *start* directory (defaults to CWD)
    2. ``~/.config/showrunner/show.toml``

    Returns ``None`` when no file is found (use built-in defaults).
    """
    if start is None:
        start = Path.cwd()

    local = start / CONFIG_FILENAME
    if local.is_file():
        return local

    global_path = Path.home() / ".config" / "showrunner" / CONFIG_FILENAME
    if global_path.is_file():
        return global_path

    return None


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def _parse_toml(path: Path) -> dict[str, Any]:
    """Read and parse a TOML file, returning a raw dict."""
    with open(path, "rb") as f:
        return tomllib.load(f)


def _extract_plugin_settings(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Pull ``[plugins.<name>]`` sub-tables out of the raw plugins section."""
    plugins_section = raw.get("plugins", {})
    return {
        key: value for key, value in plugins_section.items() if isinstance(value, dict)
    }


def load_config(path: Path | None = None) -> ShowRunnerConfig:
    """Load configuration from a TOML file.

    If *path* is ``None``, calls :func:`find_config` to locate the file.
    When no file exists at all, returns a config populated entirely with
    defaults.
    """
    if path is None:
        path = find_config()

    if path is None or not path.is_file():
        logger.debug("No config file found — using defaults")
        return ShowRunnerConfig()

    logger.info("Loading config from %s", path)
    raw = _parse_toml(path)

    # Top-level [showrunner] section
    sr_section = raw.get("showrunner", {})
    current_show = sr_section.get("current-show")

    # Per-plugin sub-tables
    plugin_settings = _extract_plugin_settings(raw)

    # Build plugins config from the flat keys + nested settings
    plugins_raw = raw.get("plugins", {})
    plugins_cfg = PluginsConfig(
        disabled=plugins_raw.get("disabled", []),
        settings=plugin_settings,
    )

    cfg = ShowRunnerConfig(
        current_show=current_show,
        database=DatabaseConfig(**raw.get("database", {})),
        server=ServerConfig(
            **{k.replace("-", "_"): v for k, v in raw.get("server", {}).items()}
        ),
        logging=LoggingConfig(**raw.get("logging", {})),
        paths=PathsConfig(**raw.get("paths", {})),
        plugins=plugins_cfg,
    )
    cfg._source_path = path
    return cfg


# ---------------------------------------------------------------------------
# Live-reload file watcher
# ---------------------------------------------------------------------------


class ConfigWatcher:
    """Watch the config file for changes and notify via a pluggy hook.

    Uses ``watchfiles`` (async) to monitor the TOML file.  On a valid
    change the ``showrunner_config_changed`` hook is invoked with the new
    and previous config.  Invalid TOML is logged and silently ignored so
    the running config is never corrupted.
    """

    def __init__(self, path: Path, app: Any) -> None:
        self._path = path
        self._app = app
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        """Spawn the background watcher task (must be called inside a running event loop)."""
        loop = asyncio.get_running_loop()
        self._task = loop.create_task(self._watch())

    async def _watch(self) -> None:
        from watchfiles import awatch

        try:
            async for _changes in awatch(self._path):
                try:
                    new_config = load_config(self._path)
                except Exception:
                    logger.warning(
                        "Config file changed but failed to parse — keeping previous config",
                        exc_info=True,
                    )
                    continue

                previous = self._app.config
                self._app.config = new_config
                logger.info("Config reloaded from %s", self._path)

                try:
                    self._app.pm.hook.showrunner_config_changed(
                        config=new_config,
                        previous_config=previous,
                    )
                except Exception:
                    logger.warning("Error in config_changed hook", exc_info=True)
        except asyncio.CancelledError:
            return

    def stop(self) -> None:
        """Cancel the watcher task."""
        if self._task is not None:
            self._task.cancel()
            self._task = None
