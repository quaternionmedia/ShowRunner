"""ShowComms - Crew communication and coordination."""

from fastapi import APIRouter

import showrunner

router = APIRouter(prefix="/comms", tags=["ShowComms"])


@router.get("/")
async def index():
    return {"plugin": "ShowComms", "status": "ok"}


class ShowCommsPlugin:
    """Communication tool for crew coordination.

    Messaging and cue notifications during a performance.
    """

    @showrunner.hookimpl
    def showrunner_register(self):
        return {
            "name": "ShowComms",
            "description": "Crew communication, messaging, and cue notifications",
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
