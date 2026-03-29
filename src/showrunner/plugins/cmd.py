"""ShowCmd - Command-line and TUI interface."""

from fastapi import APIRouter

import showrunner

router = APIRouter(prefix="/cmd", tags=["ShowCmd"])


@router.get("/")
async def index():
    return {"plugin": "ShowCmd", "status": "ok"}


class ShowCmdPlugin:
    """Command-line interface for the ShowRunner system.

    Interact via CLI or TUI.
    """

    @showrunner.hookimpl
    def showrunner_register(self):
        return {
            "name": "ShowCmd",
            "description": "CLI and TUI interface for ShowRunner",
            "version": "0.1.0",
        }

    @showrunner.hookimpl
    def showrunner_startup(self, app):
        pass

    @showrunner.hookimpl
    def showrunner_shutdown(self, app):
        pass

    @showrunner.hookimpl
    def showrunner_get_routes(self):
        return router

    @showrunner.hookimpl
    def showrunner_get_commands(self):
        return []
