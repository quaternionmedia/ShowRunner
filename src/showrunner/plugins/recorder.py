"""ShowRecorder - Archival, annotation, and review tools."""

from fastapi import APIRouter

import showrunner

router = APIRouter(prefix="/recorder", tags=["ShowRecorder"])


@router.get("/")
async def index():
    return {"plugin": "ShowRecorder", "status": "ok"}


class ShowRecorderPlugin:
    """Archiving, annotating, and reviewing rehearsals and performances.

    Includes cue logs and performance notes.
    """

    @showrunner.hookimpl
    def showrunner_register(self):
        return {
            "name": "ShowRecorder",
            "description": "Archive, annotate, and review rehearsals and performances",
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
