"""ShowRecorder - OBS websocket integration for screen recording.

Connects to OBS Studio via obs-websocket v5 to switch scenes and
control recording. Cue notes drive scene names; CueLog records each
take with timing data. Also generates MLT XML for Kdenlive assembly.

Requires OBS Studio 28+ with the built-in WebSocket server enabled:
  Tools → WebSocket Server Settings → Enable WebSocket server.

Dependencies (optional — graceful degradation when absent):
  pip install simpleobsws
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlmodel import select

import showrunner
from showrunner.models import CueLog, Show

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/recorder", tags=["ShowRecorder"])

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEFAULT_HOST = "localhost"
_DEFAULT_PORT = 4455
_DEFAULT_PASSWORD = ""


def _get_cfg(app: Any) -> dict[str, Any]:
    """Return plugin settings from show.toml [plugins.recorder]."""
    return getattr(app, "config", None) and app.config.plugins.settings.get(
        "recorder", {}
    ) or {}


async def _obs_request(app: Any, request_type: str, request_data: dict | None = None):
    """Send a single request to OBS websocket and return the response data.

    Returns ``None`` and logs a warning when simpleobsws is not installed or
    OBS is unreachable — allows the API to stay up even without OBS running.
    """
    try:
        import simpleobsws
    except ImportError:
        logger.warning("simpleobsws not installed — OBS integration unavailable")
        return None

    cfg = _get_cfg(app)
    host = cfg.get("obs-host", _DEFAULT_HOST)
    port = cfg.get("obs-port", _DEFAULT_PORT)
    password = cfg.get("obs-password", _DEFAULT_PASSWORD)

    url = f"ws://{host}:{port}"
    params = simpleobsws.IdentificationParameters(ignoreNonFatalRequestChecks=False)
    ws = simpleobsws.WebSocketClient(url=url, password=password, identification_parameters=params)

    try:
        await ws.connect()
        await ws.wait_until_identified()
        req = simpleobsws.Request(request_type, request_data or {})
        result = await ws.call(req)
        return result.responseData if result.ok() else None
    except Exception as exc:
        logger.warning("OBS request %s failed: %s", request_type, exc)
        return None
    finally:
        try:
            await ws.disconnect()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# MLT XML generation
# ---------------------------------------------------------------------------

def _find_obs_recordings(output_dir: str) -> list[Path]:
    """Return video files from the OBS output directory, sorted oldest-first."""
    if not output_dir:
        return []
    p = Path(output_dir).expanduser()
    if not p.is_dir():
        return []
    return sorted(
        [f for f in p.iterdir() if f.suffix.lower() in (".mkv", ".mp4", ".mov")],
        key=lambda f: f.stat().st_mtime,
    )


def _naive_dt(dt: datetime) -> datetime:
    """Strip tzinfo so naive/aware datetimes can be subtracted safely."""
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


def _build_mlt_xml(
    logs: list[CueLog],
    recording_start: datetime | None,
    obs_output_dir: str = "",
) -> str:
    """Generate a Kdenlive-compatible MLT XML project.

    Each CueLog entry is a clip boundary; in/out points come from
    ``triggered_at`` offsets relative to the first log entry.

    When ``obs_output_dir`` is set (via ``[plugins.recorder] obs-output-dir``),
    OBS recording files are matched to ``<producer>`` elements so Kdenlive
    loads media directly.  Without it, stub producers are emitted with a
    ``<!-- TODO -->`` comment so the structure is valid but media must be
    linked manually in Kdenlive.
    """
    if not logs:
        return '<mlt version="7.22.0"><playlist id="main_bin"/></mlt>'

    base_time = (
        _naive_dt(recording_start) if recording_start else _naive_dt(logs[0].triggered_at)
    )
    recordings = _find_obs_recordings(obs_output_dir)
    producers: list[str] = []
    entries: list[str] = []

    for i, log in enumerate(logs):
        if log.triggered_at is None:
            continue
        offset_ms = int((_naive_dt(log.triggered_at) - base_time).total_seconds() * 1000)
        duration_ms = log.duration_ms or 5000
        notes = log.notes or ""
        scene = "unknown"
        try:
            data = json.loads(notes)
            scene = data.get("scene", scene)
        except (json.JSONDecodeError, TypeError):
            if notes:
                scene = notes

        if i < len(recordings):
            producers.append(f'  <producer id="clip_{i}" resource="{recordings[i].as_posix()}"/>')
        else:
            producers.append(
                f'  <!-- TODO: set resource path for clip_{i} ({scene}) -->\n'
                f'  <producer id="clip_{i}"/>'
            )

        entries.append(
            f'  <entry in="{offset_ms}ms" out="{offset_ms + duration_ms}ms"'
            f' producer="clip_{i}" comment="{scene}"/>'
        )

    producers_xml = "\n".join(producers)
    entries_xml = "\n".join(entries)
    return (
        '<mlt version="7.22.0">\n'
        f"{producers_xml}\n"
        '  <playlist id="main_bin">\n'
        f"{entries_xml}\n"
        "  </playlist>\n"
        "</mlt>"
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/")
async def index():
    return {"plugin": "ShowRecorder", "status": "ok"}


@router.get("/status")
async def status():
    """Check OBS connection and recording state."""
    import showrunner.plugins.recorder as _self
    app = _self._app_ref
    if app is None:
        return {"obs": "not_started", "recording": False}
    result = await _obs_request(app, "GetRecordStatus")
    if result is None:
        return {"obs": "not_connected", "recording": False}
    return {"obs": "connected", "recording": bool(result.get("outputActive", False))}


@router.post("/scene")
async def switch_scene(scene: str = Query(..., description="OBS scene name to activate")):
    """Switch the active OBS scene by name.

    Returns 200 with ``switched=false`` when OBS is not running rather than
    502, so HTTP-keyed cues in ShowProgrammer degrade gracefully.
    """
    from showrunner.plugins.recorder import _app_ref
    if _app_ref is None:
        raise HTTPException(status_code=503, detail="Plugin not yet started")
    result = await _obs_request(_app_ref, "SetCurrentProgramScene", {"sceneName": scene})
    if result is None:
        logger.info("Scene switch to %r skipped — OBS not connected", scene)
        return {"scene": scene, "switched": False, "warning": "OBS not connected"}
    return {"scene": scene, "switched": True}


@router.post("/record")
async def control_record(action: str = Query(..., description="start or stop")):
    """Start or stop OBS recording.

    Returns 200 with ``ok=false`` when OBS is not running rather than 502.
    """
    from showrunner.plugins.recorder import _app_ref
    if _app_ref is None:
        raise HTTPException(status_code=503, detail="Plugin not yet started")
    request_type = "StartRecord" if action == "start" else "StopRecord"
    result = await _obs_request(_app_ref, request_type)
    if result is None:
        logger.info("Record %r skipped — OBS not connected", action)
        return {"action": action, "ok": False, "warning": "OBS not connected"}
    return {"action": action, "ok": True}


@router.get("/export/mlt")
async def export_mlt(show_id: int = Query(..., description="Show ID to export")):
    """Generate a Kdenlive MLT XML project from CueLog entries."""
    from showrunner.plugins.recorder import _app_ref
    if _app_ref is None:
        raise HTTPException(status_code=503, detail="Plugin not yet started")

    db = getattr(_app_ref, "db", None)
    if db is None:
        raise HTTPException(status_code=503, detail="Database not available")

    with db.session() as session:
        show = session.get(Show, show_id)
        if show is None:
            raise HTTPException(status_code=404, detail=f"Show {show_id} not found")
        logs = session.exec(
            select(CueLog)
            .where(CueLog.show_id == show_id)
            .order_by(CueLog.triggered_at)
        ).all()

    cfg = _get_cfg(_app_ref)
    obs_output_dir = cfg.get("obs-output-dir", "")
    mlt = _build_mlt_xml(list(logs), None, obs_output_dir=obs_output_dir)
    return {"show_id": show_id, "mlt": mlt}


# ---------------------------------------------------------------------------
# Plugin class
# ---------------------------------------------------------------------------

# Module-level app reference set during startup so route handlers can reach it.
_app_ref: Any = None


class ShowRecorderPlugin:
    """OBS websocket integration for screen capture and recording.

    Switches scenes and starts/stops recording via the OBS WebSocket v5 API.
    Exports CueLog timing data as Kdenlive MLT XML for post-production assembly.

    Configure in show.toml::

        [plugins.recorder]
        obs-host = "localhost"
        obs-port = 4455
        obs-password = ""
    """

    @showrunner.hookimpl
    def showrunner_register(self):
        return {
            "name": "ShowRecorder",
            "description": "OBS screen recording control and Kdenlive MLT export",
            "version": "0.2.0",
        }

    @showrunner.hookimpl
    def showrunner_startup(self, app):
        global _app_ref
        _app_ref = app
        cfg = _get_cfg(app)
        host = cfg.get("obs-host", _DEFAULT_HOST)
        port = cfg.get("obs-port", _DEFAULT_PORT)
        logger.info("ShowRecorder ready — OBS target: ws://%s:%s", host, port)

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
