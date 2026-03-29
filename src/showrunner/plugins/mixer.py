"""ShowMixer - Sound mixer monitoring and control."""

from fastapi import APIRouter

import showrunner

router = APIRouter(prefix="/mixer", tags=["ShowMixer"])


@router.get("/")
async def index():
    return {"plugin": "ShowMixer", "status": "ok"}


class ShowMixerPlugin:
    """Operate sound mixers to monitor and control channels and effects.

    Supports Behringer, Allen & Heath, and other consoles.
    """

    @showrunner.hookimpl
    def showrunner_register(self):
        return {
            "name": "ShowMixer",
            "description": "Sound mixer monitoring and control (Behringer, A&H, etc.)",
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
