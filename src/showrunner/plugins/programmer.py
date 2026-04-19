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
_active_cue_list_id: int | None = None

# Timing state — module-level so REST calls and the UI page share the same
# values.  All datetimes are naive UTC (consistent with CueLog.triggered_at).
_last_fire_at: datetime | None = None   # wall-time of most-recent cue fire
_last_fire_name: str = ""               # formatted label of that cue
_show_start_at: datetime | None = None  # wall-time of first cue fire (session)


def _get_cfg(app: Any) -> dict[str, Any]:
    """Return plugin settings from show.toml [plugins.programmer]."""
    return getattr(app, "config", None) and app.config.plugins.settings.get(
        "programmer", {}
    ) or {}


def _self_base_url(app: Any) -> str:
    """Return the ShowRunner base URL for internal HTTP cue dispatch."""
    config = getattr(app, "config", None)
    if config is not None:
        host = config.server.host
        port = config.server.port
        # Bind address 0.0.0.0 means loopback for internal calls
        if host in ("0.0.0.0", ""):
            host = "127.0.0.1"
        return f"http://{host}:{port}"
    return "http://127.0.0.1:8000"


def _dispatch_http(app: Any, method: str, path: str, params: dict | None) -> bool:
    """Make an internal HTTP request to another ShowRunner endpoint.

    Used by cues with an ``"http"`` key in their notes JSON::

        {"http": {"method": "POST", "path": "/recorder/scene", "params": {"scene": "Terminal"}}}

    Returns True on 2xx, False otherwise.  Never raises.
    """
    try:
        import httpx
    except ImportError:
        logger.warning("httpx not installed — HTTP cue dispatch unavailable (pip install httpx)")
        return False

    base = _self_base_url(app)
    url = base + path
    try:
        resp = httpx.request(method.upper(), url, params=params or {}, timeout=5.0)
        logger.debug("HTTP cue → %s %s  status=%s", method.upper(), url, resp.status_code)
        return resp.is_success
    except Exception as exc:
        logger.warning("HTTP cue dispatch to %s failed: %s", url, exc)
        return False


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


async def _fire_cue(app: Any, cue: Cue, show_id: int) -> dict:
    """Execute a cue: parse notes, dispatch OSC/HTTP actions, write CueLog.

    Cue notes JSON schema::

        {
            "osc":  {"address": "/ardour/transport_play", "args": []},
            "http": {"method": "POST", "path": "/recorder/scene", "params": {"scene": "Terminal"}}
        }

    Both keys are optional and independent — a cue can carry either, both, or neither.

    Timing globals are updated **before** any I/O so the programmer page clock
    starts within one 0.1 s tick of the button press regardless of how long
    the downstream dispatch takes.  HTTP dispatch runs in a thread pool so the
    async event loop (and the UI) stay responsive during the round-trip.

    Returns a summary dict suitable for the API response.
    """
    import asyncio

    # Timing globals first — clock starts the instant _fire_cue is entered,
    # before network I/O.  Both write site and the _tick read site use naive
    # local time (datetime.now()) so elapsed subtraction is always correct.
    global _last_fire_at, _last_fire_name, _show_start_at
    num_str = str(cue.number) + (f".{cue.point}" if cue.point else "")
    _last_fire_at = datetime.now()
    _last_fire_name = f"{num_str} — {cue.name or ''}"
    if _show_start_at is None:
        _show_start_at = _last_fire_at

    cfg = _get_cfg(app)
    targets: list[dict] = cfg.get("osc-targets", _DEFAULT_OSC_TARGETS)

    osc_address: str | None = None
    osc_args: list = []
    osc_sent = 0
    http_ok: bool | None = None

    if cue.notes:
        try:
            data = json.loads(cue.notes)
        except (json.JSONDecodeError, TypeError):
            logger.debug("Cue %s notes are not JSON — no dispatch", cue)
            data = {}

        # OSC dispatch (UDP — fire-and-forget, inherently non-blocking)
        osc_cfg = data.get("osc", {})
        osc_address = osc_cfg.get("address")
        osc_args = osc_cfg.get("args", [])
        if osc_address and targets:
            osc_sent = _broadcast_osc(targets, osc_address, osc_args)
        elif osc_address and not targets:
            logger.info("Cue %s has OSC payload but no targets configured", cue)

        # HTTP dispatch — run in a thread so the event loop stays responsive
        # during the round-trip (e.g. OBS WebSocket via ShowRecorder).
        http_cfg = data.get("http", {})
        if http_cfg:
            http_ok = await asyncio.to_thread(
                _dispatch_http,
                app,
                method=http_cfg.get("method", "POST"),
                path=http_cfg.get("path", "/"),
                params=http_cfg.get("params"),
            )
            status_str = "ok" if http_ok else "failed"
            logger.info("HTTP cue %s → %s: %s", cue, http_cfg.get("path"), status_str)

    # Write CueLog — store naive UTC so SQLite round-trip is consistent
    db = getattr(app, "db", None)
    if db is not None:
        log_entry = CueLog(
            show_id=show_id,
            cue_id=cue.id,
            triggered_at=datetime.now(timezone.utc).replace(tzinfo=None),
            notes=json.dumps({"scene": cue.name, "osc_address": osc_address}),
        )
        with db.session() as session:
            session.add(log_entry)
            session.commit()

    result = {
        "cue": str(cue),
        "number": cue.number,
        "point": cue.point,
        "name": cue.name,
        "osc_address": osc_address,
        "osc_targets_reached": osc_sent,
    }
    if http_ok is not None:
        result["http_ok"] = http_ok
    return result


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

    result = await _fire_cue(_app_ref, cue, show_id)
    return result


@router.post("/go")
async def go(
    cue_list_id: int = Query(..., description="Active cue list ID"),
    show_id: int = Query(..., description="Show ID for CueLog"),
):
    """Advance to and fire the next cue in the loaded sequence."""
    global _cue_pointer, _active_cues, _active_cue_list_id
    from showrunner.plugins.programmer import _app_ref
    if _app_ref is None:
        raise HTTPException(status_code=503, detail="Plugin not yet started")
    db = getattr(_app_ref, "db", None)
    if db is None:
        raise HTTPException(status_code=503, detail="Database not available")

    # Reset pointer when switching to a different cue list
    if cue_list_id != _active_cue_list_id:
        _cue_pointer = 0
        _active_cue_list_id = cue_list_id

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
    result = await _fire_cue(_app_ref, cue, show_id)
    _cue_pointer += 1
    result["pointer"] = _cue_pointer
    return result


@router.post("/reset")
async def reset():
    """Reset the cue pointer and all timing state to zero."""
    global _cue_pointer, _last_fire_at, _last_fire_name, _show_start_at
    _cue_pointer = 0
    _last_fire_at = None
    _last_fire_name = ""
    _show_start_at = None
    return {"pointer": 0}


# ---------------------------------------------------------------------------
# Timing helpers  (module-level so they are importable by tests)
# ---------------------------------------------------------------------------


def _fmt(seconds: float) -> str:
    """Format elapsed seconds as ``HH:MM:SS`` or ``MM:SS`` (no sub-seconds).

    Used for the wall clock and the SHOW elapsed timer where second-level
    resolution is sufficient.

    >>> _fmt(0)
    '00:00'
    >>> _fmt(65)
    '01:05'
    >>> _fmt(3661)
    '01:01:01'
    """
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def _fmt_ms(seconds: float) -> str:
    """Format elapsed seconds as ``[HH:]MM:SS.cc`` (centiseconds).

    Used for the CUE elapsed timer and per-cue duration column where
    sub-second resolution is useful for timing work.

    >>> _fmt_ms(0)
    '00:00.00'
    >>> _fmt_ms(1.5)
    '00:01.50'
    >>> _fmt_ms(65.25)
    '01:05.25'
    >>> _fmt_ms(3661.99)
    '01:01:01.99'
    """
    cs = min(int(round((seconds % 1) * 100)), 99)
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    prefix = f"{h:02d}:" if h else ""
    return f"{prefix}{m:02d}:{sec:02d}.{cs:02d}"


def _cue_durations_from_logs(logs: list) -> dict[int, int]:
    """Compute ``{cue_id: duration_ms}`` from a time-ordered list of CueLog rows.

    Duration is taken from ``CueLog.duration_ms`` when explicitly set,
    otherwise inferred as the delta between consecutive log entries.  Only
    the *most recent* firing of each cue is retained, so the dict reflects
    the last known run-time for each cue.  The final log entry (the cue that
    is still running) is intentionally omitted because its duration is not
    yet known.

    Args:
        logs: CueLog instances sorted ascending by ``triggered_at``.

    Returns:
        Dict mapping cue_id to duration in milliseconds.  Empty when the
        list is empty or contains only one entry.
    """
    durations: dict[int, int] = {}
    for i in range(len(logs) - 1):
        curr = logs[i]
        nxt = logs[i + 1]
        if curr.cue_id is None:
            continue
        if curr.duration_ms is not None:
            durations[curr.cue_id] = curr.duration_ms
        else:
            # Strip tz so naive/aware timestamps subtract cleanly
            t0 = curr.triggered_at.replace(tzinfo=None) if getattr(
                curr.triggered_at, "tzinfo", None
            ) else curr.triggered_at
            t1 = nxt.triggered_at.replace(tzinfo=None) if getattr(
                nxt.triggered_at, "tzinfo", None
            ) else nxt.triggered_at
            delta_ms = int((t1 - t0).total_seconds() * 1000)
            if delta_ms > 0:
                durations[curr.cue_id] = delta_ms
    return durations


# ---------------------------------------------------------------------------
# NiceGUI page
# ---------------------------------------------------------------------------


def _build_page() -> None:
    """Register the /programmer NiceGUI page.

    The page provides:
    * Show + cue-list selectors (self-contained, no dependency on header state).
    * A timing panel (top-right) with three rows:

      =========  ===========================================================
      TIME       Wall clock (HH:MM:SS).  Flashes green when a cue fires and
                 fades back to grey over ~5 s.
      SHOW       Elapsed time since the first GO in this browser session
                 (resets on RESET).  Resolution: seconds.
      CUE        Elapsed time since the most-recent GO.  Resolution:
                 centiseconds (MM:SS.cc).  Amber after 5 s.
      =========  ===========================================================

    * A cue stack showing all cues for the selected list.  The most-recently
      fired (in-flight) cue shows a live amber elapsed timer updated every
      0.1 s.  Older fired cues are dimmed and annotated with their last-known
      duration (MM:SS.cc) derived from CueLog timestamps.  The next-to-fire
      cue is highlighted green with a play icon.
    * GO / RESET buttons.

    Implementation notes
    --------------------
    The timing panel reads three module-level globals (``_last_fire_at``,
    ``_last_fire_name``, ``_show_start_at``) that are written by
    :func:`_fire_cue` on every cue dispatch — whether the call originated from
    the GO button, a REST ``POST /programmer/go``, or ``POST /programmer/cue``.
    Both the write site and the read site (``_tick``) use **naive local time**
    (``datetime.now()`` with no tzinfo) so elapsed-time subtraction is always
    correct regardless of the server's UTC offset.
    """
    from nicegui import app as nicegui_app
    from nicegui import ui
    from sqlmodel import select

    from showrunner.models import Cue, CueList, CueLog
    from showrunner.plugins.db import get_db
    from showrunner.ui import header

    def _load_shows() -> dict[int, str]:
        try:
            from showrunner.models import Show
            with get_db().session() as s:
                shows = s.exec(select(Show).order_by(Show.name)).all()
                return {sh.id: sh.name for sh in shows}
        except Exception:
            return {}

    def _load_cue_lists(show_id: int | None) -> dict[int, str]:
        if show_id is None:
            return {}
        with get_db().session() as s:
            lists = s.exec(
                select(CueList).where(CueList.show_id == show_id).order_by(CueList.id)
            ).all()
            return {cl.id: cl.name for cl in lists}

    def _load_cues(cue_list_id: int | None) -> list[Cue]:
        if cue_list_id is None:
            return []
        with get_db().session() as s:
            return list(
                s.exec(
                    select(Cue)
                    .where(Cue.cue_list_id == cue_list_id)
                    .order_by(Cue.number, Cue.point)
                ).all()
            )

    def _load_cue_durations(show_id: int | None) -> dict[int, int]:
        """Return ``{cue_id: duration_ms}`` from recent CueLog entries."""
        if show_id is None:
            return {}
        try:
            with get_db().session() as s:
                logs = list(
                    s.exec(
                        select(CueLog)
                        .where(CueLog.show_id == show_id)
                        .where(CueLog.cue_id.isnot(None))  # type: ignore[attr-defined]
                        .order_by(CueLog.triggered_at)
                    ).all()
                )
            return _cue_durations_from_logs(logs)
        except Exception:
            return {}

    @ui.page("/programmer")
    async def programmer_page():
        ui.dark_mode(True)
        header()

        shows = _load_shows()
        stored_show = nicegui_app.storage.general.get("current_show")
        if stored_show in shows:
            initial_show_id = stored_show
        elif shows:
            initial_show_id = next(iter(shows))
        else:
            initial_show_id = None

        cue_lists = _load_cue_lists(initial_show_id)
        stored_list = nicegui_app.storage.general.get("programmer_cue_list_id")
        if stored_list in cue_lists:
            initial_list_id = stored_list
        elif cue_lists:
            initial_list_id = next(iter(cue_lists))
        else:
            initial_list_id = None

        state: dict[str, Any] = {
            "show_id": initial_show_id,
            "cue_list_id": initial_list_id,
        }
        # NiceGUI element reference for the in-flight cue's live timer label.
        # Set inside cue_stack() on each refresh; read inside _tick().
        refs: dict[str, Any] = {"inflight_lbl": None}

        with ui.column().classes("w-full max-w-3xl mx-auto mt-6 px-4 gap-4"):
            # Title row + timing panel
            with ui.row().classes("w-full items-start justify-between"):
                ui.label("Programmer").classes("text-h5 font-bold")

                # Right-side timing panel: wall clock + show elapsed + cue duration
                with ui.element("div").classes(
                    "rounded border border-grey-8 px-4 py-2"
                ).style("min-width: 13rem;"):
                    with ui.row().classes("items-center justify-between gap-4"):
                        ui.label("TIME").classes("text-caption text-grey-6 font-mono")
                        clock_lbl = ui.label("--:--:--").classes(
                            "text-h5 font-mono tracking-widest text-grey-4"
                        )
                    with ui.row().classes("items-center justify-between gap-4"):
                        ui.label("SHOW").classes("text-caption text-grey-6 font-mono")
                        show_lbl = ui.label("--:--").classes(
                            "text-body1 font-mono tracking-wide text-grey-6"
                        )
                    with ui.row().classes("items-center justify-between gap-4"):
                        ui.label(" CUE").classes("text-caption text-grey-6 font-mono")
                        cue_lbl = ui.label("--:--.--").classes(
                            "text-body1 font-mono tracking-wide text-grey-6"
                        )
                    cue_name_lbl = ui.label("").classes(
                        "text-caption text-grey-7 truncate w-full text-right"
                    ).style("max-width: 13rem;")

            # Tick every 0.1 s — reads module-level globals so every fire path
            # (UI button, REST API, curl) drives the clock without polling.
            def _tick():
                from datetime import datetime as _local_dt
                now = _local_dt.now()
                clock_lbl.set_text(now.strftime("%H:%M:%S"))

                cue_t = _last_fire_at   # module global, updated by _fire_cue
                show_t = _show_start_at  # module global, set on first fire

                # Wall-clock + CUE row
                if cue_t is not None:
                    cue_elapsed = (now - cue_t).total_seconds()
                    if cue_elapsed < 1.5:
                        clock_lbl.classes(
                            replace="text-h5 font-mono tracking-widest text-green-4"
                        )
                    elif cue_elapsed < 5:
                        clock_lbl.classes(
                            replace="text-h5 font-mono tracking-widest text-green-7"
                        )
                    else:
                        clock_lbl.classes(
                            replace="text-h5 font-mono tracking-widest text-grey-4"
                        )
                    cue_lbl.set_text(_fmt_ms(cue_elapsed))
                    cue_lbl.classes(
                        replace="text-body1 font-mono tracking-wide "
                        + ("text-green-5" if cue_elapsed < 5 else "text-amber-6")
                    )
                    cue_name_lbl.set_text(_last_fire_name)
                    # Live in-flight timer inside the cue stack row
                    lbl = refs["inflight_lbl"]
                    if lbl is not None:
                        lbl.set_text(_fmt_ms(cue_elapsed))
                else:
                    clock_lbl.classes(
                        replace="text-h5 font-mono tracking-widest text-grey-4"
                    )
                    cue_lbl.set_text("--:--.--")
                    cue_lbl.classes(
                        replace="text-body1 font-mono tracking-wide text-grey-6"
                    )
                    cue_name_lbl.set_text("")
                    refs["inflight_lbl"] = None

                # SHOW row
                if show_t is not None:
                    show_elapsed = (now - show_t).total_seconds()
                    show_lbl.set_text(_fmt(show_elapsed))
                    show_lbl.classes(
                        replace="text-body1 font-mono tracking-wide text-grey-3"
                    )
                else:
                    show_lbl.set_text("--:--")
                    show_lbl.classes(
                        replace="text-body1 font-mono tracking-wide text-grey-6"
                    )

            ui.timer(0.1, _tick)

            feedback = ui.label("").classes("text-caption text-grey-5")

            # Show + cue-list selectors
            with ui.row().classes("items-center gap-4 w-full flex-wrap"):
                if not shows:
                    ui.label(
                        "No shows found — run: python examples/setup_intro.py"
                    ).classes("text-grey-5")
                else:
                    @ui.refreshable
                    def cue_list_selector():
                        cl = _load_cue_lists(state["show_id"])
                        cl_val = state["cue_list_id"] if state["cue_list_id"] in cl else (
                            next(iter(cl), None)
                        )
                        state["cue_list_id"] = cl_val

                        def _on_list_change(e):
                            state["cue_list_id"] = e.value
                            nicegui_app.storage.general["programmer_cue_list_id"] = e.value
                            cue_stack.refresh()

                        if cl:
                            ui.select(
                                options=cl,
                                value=cl_val,
                                label="Cue List",
                                on_change=_on_list_change,
                            ).classes("min-w-44")
                        else:
                            ui.label("No cue lists for this show.").classes("text-grey-5")

                    def _on_show_change(e):
                        state["show_id"] = e.value
                        nicegui_app.storage.general["current_show"] = e.value
                        cue_list_selector.refresh()
                        cue_stack.refresh()

                    ui.select(
                        options=shows,
                        value=initial_show_id,
                        label="Show",
                        on_change=_on_show_change,
                    ).classes("min-w-52")

                    cue_list_selector()

            # GO / RESET buttons
            with ui.row().classes("gap-3 items-center"):

                async def _on_go():
                    if state["cue_list_id"] is None or state["show_id"] is None:
                        feedback.set_text("Select a show and cue list first.")
                        return
                    go_btn.disable()
                    try:
                        result = await go(
                            cue_list_id=state["cue_list_id"],
                            show_id=state["show_id"],
                        )
                        num = result.get("number", "?")
                        point = result.get("point", 0)
                        name = result.get("name", "?")
                        num_str = f"{num}.{point}" if point else str(num)
                        feedback.set_text(f"GO → {num_str}  {name}")
                        feedback.classes(replace="text-caption text-green-5")
                    except Exception as exc:
                        detail = getattr(exc, "detail", str(exc))
                        feedback.set_text(str(detail))
                        feedback.classes(replace="text-caption text-red-5")
                    finally:
                        go_btn.enable()
                    cue_stack.refresh()

                go_btn = ui.button("GO", on_click=_on_go).props(
                    "color=positive size=lg"
                ).classes("px-10 text-weight-bold")

                # Space bar fires GO — standard in theatrical cue software.
                # NiceGUI's ui.keyboard ignores keypresses when an input or
                # select element is focused, preventing accidental fires.
                async def _on_key(e):
                    if e.key == " " and e.action.keydown and not e.action.repeat:
                        await _on_go()

                ui.keyboard(on_key=_on_key)

                async def _on_reset():
                    await reset()
                    feedback.set_text("Reset to top.")
                    feedback.classes(replace="text-caption text-grey-5")
                    cue_stack.refresh()

                ui.button("RESET", on_click=_on_reset).props("color=grey-7 size=md")

            # Cue stack
            @ui.refreshable
            def cue_stack():
                cues = _load_cues(state["cue_list_id"])
                if not cues:
                    ui.label("No cues in this list.").classes("text-grey-5 mt-4")
                    return

                pointer = (
                    _cue_pointer
                    if _active_cue_list_id == state["cue_list_id"]
                    else 0
                )

                # Load last-known durations from CueLog for all fired cues
                durations = _load_cue_durations(state["show_id"])

                # Reset inflight reference before rebuilding rows — will be
                # overwritten below if there is an in-flight cue this render.
                refs["inflight_lbl"] = None

                with ui.element("div").classes("w-full rounded border border-grey-8"):
                    for i, cue in enumerate(cues):
                        num_str = (
                            f"{cue.number}.{cue.point}" if cue.point else str(cue.number)
                        )
                        is_next = i == pointer
                        is_fired = i < pointer
                        # The most-recently fired cue is currently running.
                        is_inflight = is_fired and (i == pointer - 1)

                        row_bg = "background: #1b5e20;" if is_next else ""
                        text_cls = "text-grey-6" if is_fired else ""

                        with ui.row().classes(
                            f"w-full px-3 py-1 items-center gap-3 {text_cls}"
                        ).style(row_bg):
                            ui.label(num_str).classes(
                                "text-caption font-mono text-right"
                            ).style("min-width: 3rem;")
                            ui.label(cue.name or "").classes("flex-grow text-body2")
                            if cue.layer:
                                ui.badge(cue.layer).props("outline").classes(
                                    "text-grey-5"
                                )
                            # In-flight cue: live timer driven by _tick every 0.1 s
                            if is_inflight:
                                lbl = ui.label("--:--.--").classes(
                                    "text-caption font-mono text-amber-5"
                                ).style("min-width: 5rem; text-align: right;")
                                refs["inflight_lbl"] = lbl
                            # Duration badge — shown for other fired cues when available
                            elif is_fired and cue.id in durations:
                                ui.label(_fmt_ms(durations[cue.id] / 1000)).classes(
                                    "text-caption font-mono text-grey-7"
                                ).style("min-width: 5rem; text-align: right;")
                            elif is_next:
                                ui.icon("play_arrow").classes("text-green-3")

            cue_stack()


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
        global _app_ref, _cue_pointer, _active_cues, _active_cue_list_id
        global _last_fire_at, _last_fire_name, _show_start_at
        _app_ref = app
        _cue_pointer = 0
        _active_cues = []
        _active_cue_list_id = None
        _last_fire_at = None
        _last_fire_name = ""
        _show_start_at = None
        cfg = _get_cfg(app)
        targets = cfg.get("osc-targets", [])
        logger.info("ShowProgrammer ready — %d OSC target(s) configured", len(targets))
        _build_page()

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
        return {
            "label": "Programmer", "path": "/programmer",
            "icon": "queue_play_next", "order": 30,
        }

    @showrunner.hookimpl
    def showrunner_get_status(self):
        return None
