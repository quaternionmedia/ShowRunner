"""ShowLighter - Lighting control system integration."""

from fastapi import APIRouter

import showrunner

router = APIRouter(prefix="/lighter", tags=["ShowLighter"])


@router.get("/")
async def index():
    return {"plugin": "ShowLighter", "status": "ok"}


class ShowLighterPlugin:
    """Integrate cues directly with lighting control systems.

    Supports ETC Eos, Chamsys, MA Lighting, and others.
    """

    @showrunner.hookimpl
    def showrunner_register(self):
        return {
            "name": "ShowLighter",
            "description": "Lighting control integration (ETC Eos, Chamsys, MA, etc.)",
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
