"""ShowManager - Stage management for live performances."""

from fastapi import APIRouter

import showrunner

router = APIRouter(prefix="/manager", tags=["ShowManager"])


@router.get("/")
async def index():
    return {"plugin": "ShowManager", "status": "ok"}


class ShowManagerPlugin:
    """Stage management tools for live performances.

    Manage and trigger cues during a show.
    """

    @showrunner.hookimpl
    def showrunner_register(self):
        return {
            "name": "ShowManager",
            "description": "Stage management and cue triggering for live shows",
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
