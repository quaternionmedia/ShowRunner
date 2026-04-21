"""ShowRunner core application.

Manages the plugin lifecycle and wires plugins into the FastAPI application.
"""

from __future__ import annotations

from loguru import logger
from pathlib import Path

from fastapi import FastAPI

from .config import ConfigWatcher, load_config
from .utils import get_plugin_manager
from loguru import logger
from time import time
from random import choices
import string


class ShowRunner:
    """Main ShowRunner application.

    Holds the plugin manager and the FastAPI app instance.
    Provides lifecycle management for all registered plugins.
    """

    def __init__(self, config_path: Path | None = None) -> None:
        self.config = load_config(config_path)
        self._config_path = config_path or self.config._source_path

        self.pm = get_plugin_manager(self.config)
        self._config_watcher: ConfigWatcher | None = None
        self.api = FastAPI(title="ShowRunner", version="0.1.0", docs_url="/api")
        self._mount_routes()

    def _mount_routes(self) -> None:
        """Collect APIRouters from all plugins and include them in the FastAPI app."""
        routers = self.pm.hook.showrunner_get_routes()
        for router in routers:
            if router is not None:
                self.api.include_router(router)

    def startup(self) -> None:
        """Invoke the startup hook on all plugins and start the config watcher."""
        self.pm.hook.showrunner_startup(app=self)

        if self._config_path is not None:
            self._config_watcher = ConfigWatcher(self._config_path, self)
            self._config_watcher.start()
            logger.info("Watching %s for changes", self._config_path)
            print(f"  Config:     {self._config_path} (watching for changes)")

    def shutdown(self) -> None:
        """Invoke the shutdown hook on all plugins and stop the config watcher."""
        if self._config_watcher is not None:
            self._config_watcher.stop()
            self._config_watcher = None
        self.pm.hook.showrunner_shutdown(app=self)

    def list_plugins(self) -> list[dict]:
        """Return metadata from all registered plugins."""
        return self.pm.hook.showrunner_register()

    def list_commands(self) -> list[dict]:
        """Collect CLI commands from all plugins."""
        results = self.pm.hook.showrunner_get_commands()
        # Flatten the list-of-lists into a single list
        return [cmd for group in results if group for cmd in group]


def _create_app() -> FastAPI:
    """Create and start a ShowRunner instance, returning its FastAPI app.

    Used as the ASGI entry point for ``uvicorn showrunner.app:app --reload``.
    """
    show = ShowRunner()

    @show.api.middleware("http")
    async def log_requests(request: Request, call_next):
        idem = ''.join(choices(string.ascii_uppercase + string.digits, k=6))
        logger.trace(f"rid={idem} start request path={request.url.path}")
        start_time = time()

        response = await call_next(request)

        process_time = (time() - start_time) * 1000
        logger.trace(
            f"rid={idem} completed_in={process_time:.3f}ms status_code={response.status_code}"
        )

        return response

    show.startup()
    return show.api


def __getattr__(name: str):
    if name == "app":
        globals()["app"] = _create_app()
        return globals()["app"]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
