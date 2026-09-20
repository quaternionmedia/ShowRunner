"""ShowMixer - Ardour OSC transport and record control.

Sends OSC messages to Ardour's built-in OSC server to control the
transport (play/stop/rewind), arm tracks for recording, and save sessions.

Enable OSC in Ardour:
  Edit → Preferences → Control Surfaces → Open Sound Control (OSC) → Enable

Default OSC port is 3819.  The response port (Ardour→ShowRunner) is
configurable but not required for simple transport commands.

Dependencies (optional — graceful degradation when absent):
  pip install python-osc
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query

import showrunner

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mixer", tags=["ShowMixer"])

# ---------------------------------------------------------------------------
# OSC addresses used with Ardour
# ---------------------------------------------------------------------------

_OSC_ADDRESSES = {
    "play": "/ardour/transport_play",
    "stop": "/ardour/transport_stop",
    "rewind": "/ardour/goto_start",
    "record": "/ardour/rec_enable_toggle",
    "save": "/ardour/save_state",
}

_DEFAULT_HOST = "localhost"
_DEFAULT_PORT = 3819


def _get_cfg(app: Any) -> dict[str, Any]:
    """Return plugin settings from show.toml [plugins.mixer]."""
    return getattr(app, "config", None) and app.config.plugins.settings.get(
        "mixer", {}
    ) or {}


def _send_osc(app: Any, address: str, *args) -> bool:
    """Send a single OSC message to Ardour.

    Returns ``True`` on success, ``False`` when python-osc is not installed or
    the target is unreachable.  Never raises — callers decide how to handle.
    """
    try:
        from pythonosc.udp_client import SimpleUDPClient
    except ImportError:
        logger.warning("python-osc not installed — Ardour OSC integration unavailable")
        return False

    cfg = _get_cfg(app)
    host = cfg.get("ardour-host", _DEFAULT_HOST)
    port = int(cfg.get("ardour-osc-port", _DEFAULT_PORT))

    try:
        client = SimpleUDPClient(host, port)
        if args:
            client.send_message(address, list(args))
        else:
            client.send_message(address, [])
        logger.debug("OSC → %s:%s  %s  %s", host, port, address, args)
        return True
    except Exception as exc:
        logger.warning("OSC send to %s:%s failed: %s", host, port, exc)
        return False


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/")
async def index():
    return {"plugin": "ShowMixer", "status": "ok"}


@router.get("/status")
async def status():
    """Return configured Ardour OSC target."""
    from showrunner.plugins.mixer import _app_ref
    if _app_ref is None:
        return {"ardour": "not_started"}
    cfg = _get_cfg(_app_ref)
    return {
        "ardour_host": cfg.get("ardour-host", _DEFAULT_HOST),
        "ardour_osc_port": cfg.get("ardour-osc-port", _DEFAULT_PORT),
    }


@router.post("/transport")
async def transport(
    action: str = Query(..., description="play, stop, rewind, or save"),
):
    """Send a transport command to Ardour via OSC."""
    from showrunner.plugins.mixer import _app_ref
    if _app_ref is None:
        raise HTTPException(status_code=503, detail="Plugin not yet started")
    address = _OSC_ADDRESSES.get(action)
    if address is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown action '{action}'. Valid: {list(_OSC_ADDRESSES)}",
        )
    ok = _send_osc(_app_ref, address)
    if not ok:
        raise HTTPException(
            status_code=502, detail="Ardour OSC unreachable or python-osc not installed"
        )
    return {"action": action, "osc_address": address, "sent": True}


@router.post("/record")
async def record_arm():
    """Toggle record-arm on the default track."""
    from showrunner.plugins.mixer import _app_ref
    if _app_ref is None:
        raise HTTPException(status_code=503, detail="Plugin not yet started")
    ok = _send_osc(_app_ref, _OSC_ADDRESSES["record"])
    if not ok:
        raise HTTPException(
            status_code=502, detail="Ardour OSC unreachable or python-osc not installed"
        )
    return {"action": "record_arm_toggle", "sent": True}


@router.post("/osc")
async def raw_osc(
    address: str = Query(..., description="OSC address, e.g. /ardour/transport_play"),
):
    """Send an arbitrary OSC address to Ardour (advanced use)."""
    from showrunner.plugins.mixer import _app_ref
    if _app_ref is None:
        raise HTTPException(status_code=503, detail="Plugin not yet started")
    ok = _send_osc(_app_ref, address)
    if not ok:
        raise HTTPException(
            status_code=502, detail="Ardour OSC unreachable or python-osc not installed"
        )
    return {"address": address, "sent": True}


# ---------------------------------------------------------------------------
# Plugin class
# ---------------------------------------------------------------------------

_app_ref: Any = None


class ShowMixerPlugin:
    """Ardour OSC transport control for recording sessions.

    Sends play/stop/rewind/record-arm commands to Ardour's built-in OSC
    server.  Used during production recording to keep Ardour in sync with
    ShowRunner's cue timeline.

    Configure in show.toml::

        [plugins.mixer]
        ardour-host = "localhost"
        ardour-osc-port = 3819
    """

    @showrunner.hookimpl
    def showrunner_register(self):
        return {
            "name": "ShowMixer",
            "description": "Ardour OSC transport and record-arm control",
            "version": "0.2.0",
        }

    @showrunner.hookimpl
    def showrunner_startup(self, app):
        global _app_ref
        _app_ref = app
        cfg = _get_cfg(app)
        host = cfg.get("ardour-host", _DEFAULT_HOST)
        port = cfg.get("ardour-osc-port", _DEFAULT_PORT)
        logger.info("ShowMixer ready — Ardour OSC target: %s:%s", host, port)

    @showrunner.hookimpl
    def showrunner_shutdown(self, app):
        global _app_ref
        _app_ref = None

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
