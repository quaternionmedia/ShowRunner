"""ShowDesigner - Cue design based on parsed scripts."""

from fastapi import APIRouter

import showrunner

router = APIRouter(prefix="/designer", tags=["ShowDesigner"])


@router.get("/")
async def index():
    return {"plugin": "ShowDesigner", "status": "ok"}


class ShowDesignerPlugin:
    """Design cues based on the parsed script.

    Set up cue layers for specific integration with other tools.
    """

    @showrunner.hookimpl
    def showrunner_register(self):
        return {
            "name": "ShowDesigner",
            "description": "Cue design from parsed scripts with layer integration",
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

    @showrunner.hookimpl
    def showrunner_get_nav(self):
        return None

    @showrunner.hookimpl
    def showrunner_get_status(self):
        return None
