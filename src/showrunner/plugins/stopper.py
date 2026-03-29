"""ShowStopper - Stopwatch and timing tools for live performances."""

from fastapi import APIRouter

import showrunner

router = APIRouter(prefix="/stopper", tags=["ShowStopper"])


@router.get("/")
async def index():
    return {"plugin": "ShowStopper", "status": "ok"}


class ShowStopperPlugin:
    """Stopwatch with helpful features for live performances.

    Includes logging and cue timing.
    """

    @showrunner.hookimpl
    def showrunner_register(self):
        return {
            "name": "ShowStopper",
            "description": "Stopwatch, logging, and cue timing tools",
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
