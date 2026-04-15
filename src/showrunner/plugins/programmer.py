"""ShowProgrammer - OSC cue dispatcher (QLab-equivalent).

Fires cues by number: looks up the Cue in the database, reads an OSC
payload from ``cue.notes`` (JSON), broadcasts it to all configured targets,
and writes a CueLog entry.  Acts as the ShowRunner-native replacement for
QLab on non-Mac platforms.

Cue ``notes`` JSON schema::

    {
        "osc": {
            "address": "/ardour/transport_play",
            "args": []
        }
    }

Multiple targets can be configured in show.toml so a single cue fires
Ardour, OBS, or any other OSC-capable tool simultaneously.

Dependencies (optional — graceful degradation when absent):
  pip install python-osc
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlmodel import select

import showrunner
from showrunner.models import Cue, CueList, CueLog

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/programmer", tags=["ShowProgrammer"])

_DEFAULT_OSC_TARGETS: list[dict] = []  # [{host, port}, ...]

# Tracks the "next cue" index across the currently active cue list.
_cue_pointer: int = 0
_active_cues: list[Cue] = []


def _get_cfg(app: Any) -> dict[str, Any]:
    """Return plugin settings from show.toml [plugins.programmer]."""
    return getattr(app, "config", None) and app.config.plugins.settings.get(
        "programmer", {}
    ) or {}


def _broadcast_osc(targets: list[dict], address: str, args: list) -> int:
    """Send an OSC message to every configured target.

    Returns the number of successful sends.  Never raises.
    """
    try:
        from pythonosc.udp_client import SimpleUDPClient
    except ImportError:
        logger.warning("python-osc not installed — OSC dispatch unavailable")
        return 0

    sent = 0
    for target in targets:
        host = target.get("host", "localhost")
        port = int(target.get("port", 9000))
        try:
            client = SimpleUDPClient(host, port)
            client.send_message(address, args)
            logger.debug("OSC → %s:%s  %s  %s", host, port, address, args)
            sent += 1
        except Exception as exc:
            logger.warning("OSC send to %s:%s failed: %s", host, port, exc)
    return sent


def _fire_cue(app: Any, cue: Cue, show_id: int) -> dict:
    """Execute a cue: parse notes, broadcast OSC, write CueLog.

    Returns a summary dict suitable for the API response.
    """
    cfg = _get_cfg(app)
    targets: list[dict] = cfg.get("osc-targets", _DEFAULT_OSC_TARGETS)

    osc_address: str | None = None
    osc_args: list = []
    osc_sent = 0

    # Parse OSC payload from cue notes (JSON)
    if cue.notes:
        try:
            data = json.loads(cue.notes)
            osc_cfg = data.get("osc", {})
            osc_address = osc_cfg.get("address")
            osc_args = osc_cfg.get("args", [])
        except (json.JSONDecodeError, TypeError):
            logger.debug("Cue %s notes are not JSON — no OSC dispatch", cue)

    if osc_address and targets:
        osc_sent = _broadcast_osc(targets, osc_address, osc_args)
    elif osc_address and not targets:
        logger.info("Cue %s has OSC payload but no targets configured", cue)

    # Write CueLog
    db = getattr(app, "db", None)
    if db is not None:
        log_entry = CueLog(
            show_id=show_id,
            cue_id=cue.id,
            triggered_at=datetime.now(timezone.utc),
            notes=json.dumps({"scene": cue.name, "osc_address": osc_address}),
        )
        with db.session() as session:
            session.add(log_entry)
            session.commit()

    return {
        "cue": str(cue),
        "number": cue.number,
        "point": cue.point,
        "name": cue.name,
        "osc_address": osc_address,
        "osc_targets_reached": osc_sent,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/")
async def index():
    return {"plugin": "ShowProgrammer", "status": "ok"}


@router.get("/cues")
async def list_cues(
    cue_list_id: int = Query(..., description="Cue list ID to display"),
):
    """Return all cues in a list, ordered by (number, point)."""
    from showrunner.plugins.programmer import _app_ref
    if _app_ref is None:
        raise HTTPException(status_code=503, detail="Plugin not yet started")
    db = getattr(_app_ref, "db", None)
    if db is None:
        raise HTTPException(status_code=503, detail="Database not available")

    with db.session() as session:
        cue_list = session.get(CueList, cue_list_id)
        if cue_list is None:
            raise HTTPException(status_code=404, detail=f"CueList {cue_list_id} not found")
        cues = session.exec(
            select(Cue)
            .where(Cue.cue_list_id == cue_list_id)
            .order_by(Cue.number, Cue.point)
        ).all()

    return {
        "cue_list": cue_list.name,
        "cues": [
            {
                "id": c.id,
                "number": c.number,
                "point": c.point,
                "name": c.name,
                "layer": c.layer,
                "notes": c.notes,
            }
            for c in cues
        ],
    }


@router.get("/status")
async def status():
    """Return current cue pointer position."""
    from showrunner.plugins.programmer import _active_cues, _cue_pointer

    current = (
        _active_cues[_cue_pointer]
        if _active_cues and _cue_pointer < len(_active_cues)
        else None
    )
    return {
        "pointer": _cue_pointer,
        "total_cues": len(_active_cues),
        "current_cue": str(current) if current else None,
    }


@router.post("/cue/{cue_number}")
async def fire_by_number(
    cue_number: str,
    cue_list_id: int = Query(..., description="Cue list ID"),
    show_id: int = Query(..., description="Show ID for CueLog"),
):
    """Fire a specific cue by number string (e.g. '4.0' or '1')."""
    from showrunner.plugins.programmer import _app_ref
    if _app_ref is None:
        raise HTTPException(status_code=503, detail="Plugin not yet started")
    db = getattr(_app_ref, "db", None)
    if db is None:
        raise HTTPException(status_code=503, detail="Database not available")

    # Parse "number" and optional "point" from the string
    parts = cue_number.split(".")
    try:
        number = int(parts[0])
        point = int(parts[1]) if len(parts) > 1 else 0
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid cue number: {cue_number!r}")

    with db.session() as session:
        cue = session.exec(
            select(Cue)
            .where(Cue.cue_list_id == cue_list_id)
            .where(Cue.number == number)
            .where(Cue.point == point)
        ).first()
        if cue is None:
            raise HTTPException(
                status_code=404,
                detail=f"Cue {cue_number} not found in cue list {cue_list_id}",
            )
        # Detach from session before passing to _fire_cue
        session.expunge(cue)

    result = _fire_cue(_app_ref, cue, show_id)
    return result


@router.post("/go")
async def go(
    cue_list_id: int = Query(..., description="Active cue list ID"),
    show_id: int = Query(..., description="Show ID for CueLog"),
):
    """Advance to and fire the next cue in the loaded sequence."""
    global _cue_pointer, _active_cues
    from showrunner.plugins.programmer import _app_ref
    if _app_ref is None:
        raise HTTPException(status_code=503, detail="Plugin not yet started")
    db = getattr(_app_ref, "db", None)
    if db is None:
        raise HTTPException(status_code=503, detail="Database not available")

    # Reload cues if list changed or first call
    with db.session() as session:
        cues = session.exec(
            select(Cue)
            .where(Cue.cue_list_id == cue_list_id)
            .order_by(Cue.number, Cue.point)
        ).all()
        cues = list(cues)
        for c in cues:
            session.expunge(c)

    _active_cues = cues

    if not _active_cues:
        raise HTTPException(status_code=404, detail="No cues in list")

    if _cue_pointer >= len(_active_cues):
        raise HTTPException(status_code=409, detail="Past end of cue list — reset to continue")

    cue = _active_cues[_cue_pointer]
    result = _fire_cue(_app_ref, cue, show_id)
    _cue_pointer += 1
    result["pointer"] = _cue_pointer
    return result


@router.post("/reset")
async def reset():
    """Reset the cue pointer to the top of the list."""
    global _cue_pointer
    _cue_pointer = 0
    return {"pointer": 0}


# ---------------------------------------------------------------------------
# Plugin class
# ---------------------------------------------------------------------------

_app_ref: Any = None


class ShowProgrammerPlugin:
    """OSC cue dispatcher — the ShowRunner-native QLab equivalent.

    Reads OSC payloads from ``Cue.notes`` (JSON) and broadcasts them to
    configured targets (Ardour, OBS, custom tools).  Maintains a sequential
    "GO" pointer for stage-manager operation.  Every fired cue is logged to
    CueLog with a timestamp.

    Configure in show.toml::

        [plugins.programmer]
        osc-targets = [
          {host = "localhost", port = 3819},
          {host = "localhost", port = 9000},
        ]
    """

    @showrunner.hookimpl
    def showrunner_register(self):
        return {
            "name": "ShowProgrammer",
            "description": "OSC cue dispatcher with sequential GO and CueLog",
            "version": "0.2.0",
        }

    @showrunner.hookimpl
    def showrunner_startup(self, app):
        global _app_ref, _cue_pointer, _active_cues
        _app_ref = app
        _cue_pointer = 0
        _active_cues = []
        cfg = _get_cfg(app)
        targets = cfg.get("osc-targets", [])
        logger.info("ShowProgrammer ready — %d OSC target(s) configured", len(targets))

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
