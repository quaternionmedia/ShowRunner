"""ShowPrompter - Teleprompter for performers and crew."""

from fastapi import APIRouter

import showrunner

router = APIRouter(prefix="/prompter", tags=["ShowPrompter"])


@router.get("/")
async def index():
    return {"plugin": "ShowPrompter", "status": "ok"}


class ShowPrompterPlugin:
    """Teleprompter application for performers and crew.

    Display scripts and cues in real time.
    """

    @showrunner.hookimpl
    def showrunner_register(self):
        return {
            "name": "ShowPrompter",
            "description": "Teleprompter for scripts and cues",
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
