"""ShowMidi - MIDI and MIDI Control Change cue support.

Provides two cue types recognised in the :class:`~showrunner.models.Cue` model:

* ``"MIDI"``   — Note On / Note Off messages
* ``"MidiCC"`` — Control Change (CC) messages

Configuration in show.toml::

    [plugins.showmidi]
    port = 0        # MIDI output port index (default: 0)
    port_name = ""  # Substring of port name to match (takes priority over index)

Install the ``midi`` optional dependency to enable MIDI output::

    pip install showrunner[midi]
    # or:  uv sync --extra midi
"""

from __future__ import annotations

try:
    import rtmidi

    HAS_RTMIDI = True
except ImportError:
    HAS_RTMIDI = False

from fastapi import APIRouter, HTTPException

import showrunner
from showrunner.plugin import ShowRunnerPlugin

router = APIRouter(prefix='/midi', tags=['ShowMidi'])


# ---------------------------------------------------------------------------
# HTTP routes
# ---------------------------------------------------------------------------


@router.get('/')
async def index():
    """ShowMidi status."""
    return {'plugin': 'ShowMidi', 'status': 'ok', 'rtmidi': HAS_RTMIDI}


@router.get('/ports')
async def list_ports():
    """List available MIDI output ports."""
    if not HAS_RTMIDI:
        raise HTTPException(status_code=503, detail='rtmidi not installed')
    out = rtmidi.MidiOut()
    ports = out.get_ports()
    del out
    return {'ports': ports}


@router.post('/note')
async def send_note(
    channel: int = 1,
    note: int = 60,
    velocity: int = 127,
    port: int = 0,
):
    """Send a MIDI Note On / Note Off message.

    - **channel**: MIDI channel 1–16 (default 1)
    - **note**: Note number 0–127 (default 60 = middle C)
    - **velocity**: Velocity 0–127 — 0 sends Note Off (default 127)
    - **port**: MIDI output port index (default 0)
    """
    if not HAS_RTMIDI:
        raise HTTPException(status_code=503, detail='rtmidi not installed')
    _send_note(port, channel, note, velocity)
    return {'sent': 'note', 'channel': channel, 'note': note, 'velocity': velocity}


@router.post('/cc')
async def send_cc(
    channel: int = 1,
    control: int = 7,
    value: int = 0,
    port: int = 0,
):
    """Send a MIDI Control Change (CC) message.

    - **channel**: MIDI channel 1–16 (default 1)
    - **control**: CC number 0–127 (default 7 = volume)
    - **value**: CC value 0–127 (default 0)
    - **port**: MIDI output port index (default 0)
    """
    if not HAS_RTMIDI:
        raise HTTPException(status_code=503, detail='rtmidi not installed')
    _send_cc(port, channel, control, value)
    return {'sent': 'cc', 'channel': channel, 'control': control, 'value': value}


# ---------------------------------------------------------------------------
# Low-level MIDI helpers
# ---------------------------------------------------------------------------


def _resolve_port(port_index: int, port_name: str | None = None) -> int:
    """Return the MIDI output port index to use.

    When *port_name* is given, searches open port names for a case-insensitive
    substring match and returns that index.  Falls back to *port_index* when no
    match is found.
    """
    if not port_name:
        return port_index
    out = rtmidi.MidiOut()
    ports = out.get_ports()
    del out
    needle = port_name.lower()
    for i, name in enumerate(ports):
        if needle in name.lower():
            return i
    return port_index


def _send_note(port: int, channel: int, note: int, velocity: int) -> None:
    """Emit a MIDI Note On (velocity > 0) or Note Off (velocity == 0) message."""
    out = rtmidi.MidiOut()
    out.open_port(port)
    status = (0x90 if velocity > 0 else 0x80) | ((channel - 1) & 0x0F)
    out.send_message([status, note & 0x7F, velocity & 0x7F])
    del out


def _send_cc(port: int, channel: int, control: int, value: int) -> None:
    """Emit a MIDI Control Change message."""
    out = rtmidi.MidiOut()
    out.open_port(port)
    status = 0xB0 | ((channel - 1) & 0x0F)
    out.send_message([status, control & 0x7F, value & 0x7F])
    del out


# ---------------------------------------------------------------------------
# Plugin class
# ---------------------------------------------------------------------------


class ShowMidiPlugin(ShowRunnerPlugin):
    """MIDI and MIDI Control Change (CC) cue type support.

    Listens for ``showrunner_command`` calls and dispatches MIDI messages:

    * ``"midi.note"`` — send a Note On / Note Off to the configured port.
    * ``"midi.cc"``   — send a Control Change to the configured port.

    **midi.note** data keys:
        channel (int, 1–16), note (int, 0–127), velocity (int, 0–127),
        port (int, optional — overrides the configured default)

    **midi.cc** data keys:
        channel (int, 1–16), control (int, 0–127), value (int, 0–127),
        port (int, optional — overrides the configured default)

    Requires ``python-rtmidi``::

        pip install showrunner[midi]

    When ``rtmidi`` is not installed the plugin registers and accepts commands
    silently without raising errors.
    """

    def __init__(self) -> None:
        super().__init__()
        self._port_index: int = 0
        self._port_name: str | None = None

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    @showrunner.hookimpl
    def showrunner_register(self) -> dict:
        return {
            'name': 'ShowMidi',
            'description': 'MIDI and MIDI Control Change (CC) cue support',
            'version': '0.1.0',
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @showrunner.hookimpl
    def showrunner_startup(self, app) -> None:
        super().showrunner_startup(app)
        cfg = app.config.plugins.settings.get('showmidi', {})
        self._apply_config(cfg)

    @showrunner.hookimpl
    def showrunner_shutdown(self, app) -> None:
        super().showrunner_shutdown(app)

    # ------------------------------------------------------------------
    # Command dispatch
    # ------------------------------------------------------------------

    @showrunner.hookimpl
    def showrunner_command(self, command_name: str, data: dict | None) -> None:
        """Dispatch ``midi.note`` and ``midi.cc`` commands."""
        if not HAS_RTMIDI:
            return
        if command_name not in ('midi.note', 'midi.cc'):
            return

        d = data or {}
        port = int(
            d.get('port', _resolve_port(self._port_index, self._port_name))
        )
        channel = int(d.get('channel', 1))

        if command_name == 'midi.note':
            note = int(d.get('note', 60))
            velocity = int(d.get('velocity', 127))
            _send_note(port, channel, note, velocity)
        else:
            control = int(d.get('control', 0))
            value = int(d.get('value', 0))
            _send_cc(port, channel, control, value)

    # ------------------------------------------------------------------
    # Live config reload
    # ------------------------------------------------------------------

    @showrunner.hookimpl
    def showrunner_config_changed(self, config, previous_config) -> None:
        cfg = config.plugins.settings.get('showmidi', {})
        self._apply_config(cfg)

    # ------------------------------------------------------------------
    # Routes / nav / status
    # ------------------------------------------------------------------

    @showrunner.hookimpl
    def showrunner_get_routes(self):
        return router

    @showrunner.hookimpl
    def showrunner_get_commands(self) -> list:
        return []

    @showrunner.hookimpl
    def showrunner_get_nav(self):
        return None

    @showrunner.hookimpl
    def showrunner_get_status(self):
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _apply_config(self, cfg: dict) -> None:
        self._port_index = int(cfg.get('port', 0))
        self._port_name = cfg.get('port_name') or None
