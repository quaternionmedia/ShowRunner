"""ShowProgrammer - Synchronization with QLab and other tools."""

from fastapi import APIRouter

import showrunner

router = APIRouter(prefix="/programmer", tags=["ShowProgrammer"])


@router.get("/")
async def index():
    return {"plugin": "ShowProgrammer", "status": "ok"}


class ShowProgrammerPlugin:
    """Synchronize with QLab and other tools.

    Automatically create and label cues from a script.
    """

    @showrunner.hookimpl
    def showrunner_register(self):
        return {
            "name": "ShowProgrammer",
            "description": "Synchronization with QLab and other cue tools",
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
