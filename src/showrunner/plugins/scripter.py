"""ShowScripter - Script viewer and OCR parser."""

from fastapi import APIRouter

import showrunner

router = APIRouter(prefix="/scripter", tags=["ShowScripter"])


@router.get("/")
async def index():
    return {"plugin": "ShowScripter", "status": "ok"}


class ShowScripterPlugin:
    """Script viewer (PDF, Fountain, etc.) and OCR parser.

    Converts scripts into a structured format for cue management.
    """

    @showrunner.hookimpl
    def showrunner_register(self):
        return {
            "name": "ShowScripter",
            "description": "Script viewer and OCR parser for cue management",
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
