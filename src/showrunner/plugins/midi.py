"""ShowMidi - persistent MIDI connections with incoming/outgoing mappings.

* Connections stay open (one rtmidi client per connection, named ``ShowRunner``).
  A connection either attaches to a system port (substring match) or creates a
  *virtual* ``ShowRunner:<name>`` port that other apps can patch to.
* Outgoing mappings are named messages (``midi.send``); incoming mappings turn
  a received Note On / CC into a ``showrunner_command`` call.
* Settings live per show in the DB ``Config`` table (key ``midi.settings``),
  falling back to ``[plugins.showmidi]`` in show.toml. Edit them at ``/midi``.

show.toml fallback::

    [plugins.showmidi]
    port_name = "Digital Piano MIDI 1"   # shorthand: one output called "default"
    # or, in full:
    auto_reconnect = true
    connections = [
      { name = "piano", port = "Digital Piano MIDI 1", direction = "out" },
      { name = "pedal", port = "Digital Piano MIDI 1", direction = "in" },
    ]
    mappings = [
      { name = "mute", connection = "piano", type = "cc", number = 7, value = 0 },
      { name = "panic", direction = "in", type = "cc", number = 64, command = "midi.panic" },
    ]
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections import deque
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from loguru import logger
from pydantic import BaseModel, Field, ValidationError, model_validator
from sqlmodel import select

try:
    import rtmidi

    HAS_RTMIDI = True
except ImportError:
    HAS_RTMIDI = False

import showrunner
from showrunner.models import Config, Show
from showrunner.plugin import ShowRunnerPlugin
from showrunner.plugins.db import get_db

router = APIRouter(prefix='/midi', tags=['ShowMidi'])
CLIENT_NAME = 'ShowRunner'
SETTINGS_KEY = 'midi.settings'
MIDI_COMMANDS = ['midi.send', 'midi.panic', 'midi.note', 'midi.cc']


# ---------------------------------------------------------------------------
# Settings models
# ---------------------------------------------------------------------------


class Connection(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    port: str = ''  # substring of the system port name; unused when virtual
    direction: Literal['in', 'out'] = 'out'
    virtual: bool = False
    enabled: bool = True

    @model_validator(mode='after')
    def _port_required(self):
        if not self.virtual and not self.port.strip():
            raise ValueError(f"connection '{self.name}' needs a port (or virtual = true)")
        return self


class Mapping(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    direction: Literal['in', 'out'] = 'out'
    connection: str = ''  # incoming: '' matches any input
    type: Literal['note', 'cc'] = 'cc'
    channel: int = Field(1, ge=1, le=16)
    number: int = Field(0, ge=0, le=127)
    value: int = Field(127, ge=0, le=127)  # outgoing velocity / CC value
    command: str = ''  # incoming: command to dispatch
    data: dict[str, Any] = Field(default_factory=dict)


class ShowMidiSettings(BaseModel):
    auto_reconnect: bool = True
    reconnect_interval: float = Field(2.0, gt=0, lt=60)
    connections: list[Connection] = Field(default_factory=list)
    mappings: list[Mapping] = Field(default_factory=list)


def decode(msg: list[int]) -> dict | None:
    """Decode Note On/Off and CC; anything else returns None."""
    if len(msg) < 3:
        return None
    kind, channel = msg[0] & 0xF0, (msg[0] & 0x0F) + 1
    if kind == 0x90:
        return {'type': 'note', 'channel': channel, 'number': msg[1], 'value': msg[2]}
    if kind == 0x80:
        return {'type': 'note', 'channel': channel, 'number': msg[1], 'value': 0}
    if kind == 0xB0:
        return {'type': 'cc', 'channel': channel, 'number': msg[1], 'value': msg[2]}
    return None


def stable_port_name(name: str) -> str:
    """Strip ALSA's trailing ``client:port`` numbers, which change on replug."""
    return re.sub(r'\s+\d+:\d+$', '', name)


# ---------------------------------------------------------------------------
# MidiManager
# ---------------------------------------------------------------------------


class MidiManager:
    """Holds open MIDI ports keyed by connection name and dispatches input."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._outputs: dict[str, Any] = {}
        self._inputs: dict[str, Any] = {}
        # one rtmidi object (= one ALSA client) per connection, reused across reconnects
        self._clients: dict[tuple[str, str, bool], Any] = {}  # (direction, name, virtual)
        self._probe: dict[str, Any] = {}  # direction -> client used for listing ports
        self.settings = ShowMidiSettings()
        self.show_id: int | None = None
        self.config: Any = None
        self.errors: dict[tuple[str, str], str] = {}  # (direction, name) -> reason
        self._pm: Any = None
        self.monitor: deque = deque(maxlen=200)
        self.seq = 0
        self.learn_until = 0.0
        self.learned: dict | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- ports --------------------------------------------------------------

    def ports(self, direction: str) -> list[str]:
        with self._lock:
            if direction not in self._probe:
                cls = rtmidi.MidiOut if direction == 'out' else rtmidi.MidiIn
                self._probe[direction] = cls(name=CLIENT_NAME)
            return self._probe[direction].get_ports()

    def _find_port_index(self, substring: str, direction: str) -> int | None:
        needle = substring.lower()
        for i, name in enumerate(self.ports(direction)):
            # skip our own ports, otherwise a vanished device "matches" ShowRunner:<name>
            if not name.startswith(f'{CLIENT_NAME}:') and needle in name.lower():
                return i
        return None

    def is_open(self, direction: str, name: str) -> bool:
        return name in (self._outputs if direction == 'out' else self._inputs)

    def _open(self, conn: Connection) -> None:
        key = (conn.direction, conn.name, conn.virtual)
        if key not in self._clients:
            cls = rtmidi.MidiOut if conn.direction == 'out' else rtmidi.MidiIn
            self._clients[key] = cls(name=CLIENT_NAME)
        port = self._clients[key]
        if conn.virtual:
            if not port.is_port_open():  # rtmidi can't reopen a virtual port; keep it open
                port.open_virtual_port(conn.name)
        else:
            idx = self._find_port_index(conn.port, conn.direction)
            if idx is None:
                raise ValueError(f"no {conn.direction}put port matching '{conn.port}'")
            port.open_port(idx, name=conn.name)
        if conn.direction == 'in':
            port.set_callback(self._on_message, conn.name)  # close_port cancels it
            self._inputs[conn.name] = port
        else:
            self._outputs[conn.name] = port

    def _try_open(self, conn: Connection) -> bool:
        key = (conn.direction, conn.name)
        try:
            self._open(conn)
        except Exception as e:
            self.errors[key] = str(e)
            logger.warning(f"MIDI {conn.direction} '{conn.name}': {e}")
            return False
        self.errors.pop(key, None)
        logger.info(f"MIDI {conn.direction} '{conn.name}' open ({conn.port or 'virtual'})")
        return True

    def _close_ports(self, keep: set | frozenset = frozenset()) -> None:
        """Detach real ports; delete clients whose key isn't in ``keep``."""
        for key, port in list(self._clients.items()):
            try:
                if key not in keep:
                    port.close_port()
                    port.delete()
                    del self._clients[key]
                elif not key[2]:  # real port: detach, _open reattaches (port may have changed)
                    port.close_port()
            except Exception:
                pass
        self._outputs.clear()
        self._inputs.clear()

    # -- lifecycle ----------------------------------------------------------

    def apply(self, settings: ShowMidiSettings) -> None:
        """Close everything and reopen per ``settings`` (so edits always take effect)."""
        with self._lock:
            self._close_ports(
                keep={(c.direction, c.name, c.virtual) for c in settings.connections if c.enabled}
            )
            self.settings = settings
            self.errors = {}
            for conn in settings.connections:
                if conn.enabled:
                    self._try_open(conn)
        self._update_thread()

    def refresh(self) -> None:
        """Drop ports whose device vanished; open ports whose device appeared."""
        with self._lock:
            for conn in self.settings.connections:
                if not conn.enabled or conn.virtual:
                    continue
                opened = self._outputs if conn.direction == 'out' else self._inputs
                present = self._find_port_index(conn.port, conn.direction) is not None
                if conn.name in opened and not present:
                    try:
                        opened.pop(conn.name).close_port()
                    except Exception:
                        pass
                    self.errors[(conn.direction, conn.name)] = 'device disconnected'
                    logger.warning(f"MIDI {conn.direction} '{conn.name}' disconnected")
                elif conn.name not in opened and present:
                    self._try_open(conn)

    def close_all(self) -> None:
        self._stop_thread()
        with self._lock:
            self._close_ports()

    def _update_thread(self) -> None:
        if not self.settings.auto_reconnect:
            self._stop_thread()
        elif not (self._thread and self._thread.is_alive()):
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._reconnect_loop, daemon=True, name='midi-reconnect'
            )
            self._thread.start()

    def _stop_thread(self) -> None:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)
        self._thread = None

    def _reconnect_loop(self) -> None:
        while not self._stop.wait(self.settings.reconnect_interval):
            try:
                self.refresh()
            except Exception as e:
                logger.warning(f'MIDI reconnect: {e}')

    # -- output -------------------------------------------------------------

    def send(self, connection: str, msg: list[int]) -> None:
        with self._lock:
            if connection not in self._outputs:
                raise KeyError(f"MIDI output '{connection}' is not open")
            self._outputs[connection].send_message(msg)

    def send_note(self, connection: str, channel: int, note: int, velocity: int) -> None:
        # shortcut: no duration_ms auto Note Off, add a threading.Timer when cues need timed notes
        kind = 0x90 if velocity > 0 else 0x80
        self.send(connection, [kind | (channel - 1) & 0x0F, note & 0x7F, velocity & 0x7F])

    def send_cc(self, connection: str, channel: int, control: int, value: int) -> None:
        self.send(connection, [0xB0 | (channel - 1) & 0x0F, control & 0x7F, value & 0x7F])

    def send_mapping(self, name: str) -> None:
        m = next(
            (m for m in self.settings.mappings if m.name == name and m.direction == 'out'),
            None,
        )
        if m is None:
            raise KeyError(f"no outgoing mapping named '{name}'")
        send = self.send_note if m.type == 'note' else self.send_cc
        send(m.connection, m.channel, m.number, m.value)

    def default_output(self) -> str | None:
        return next(
            (c.name for c in self.settings.connections if c.direction == 'out' and c.enabled),
            None,
        )

    def panic(self) -> None:
        """All Notes Off (CC 123) + All Sound Off (CC 120) on every channel and output."""
        with self._lock:
            for out in self._outputs.values():
                for ch in range(16):
                    out.send_message([0xB0 | ch, 123, 0])
                    out.send_message([0xB0 | ch, 120, 0])
        logger.info('MIDI panic sent')

    # -- input --------------------------------------------------------------

    def start_learn(self, timeout: float = 10.0) -> None:
        """Capture the next incoming message into ``learned`` instead of dispatching it."""
        self.learned = None
        self.learn_until = time.monotonic() + timeout

    def _on_message(self, event: tuple, conn_name: str) -> None:
        """rtmidi callback (runs on rtmidi's thread)."""
        msg = decode(event[0])
        if msg is None:
            return
        msg['connection'] = conn_name
        self.seq += 1
        self.monitor.append({**msg, 'seq': self.seq})
        if time.monotonic() < self.learn_until:
            self.learn_until = 0.0
            self.learned = msg
            return
        self.dispatch(msg)

    def dispatch(self, msg: dict) -> None:
        if self._pm is None:
            return
        for m in self.settings.mappings:
            if m.direction != 'in' or not m.command:
                continue
            if (m.type, m.channel, m.number) != (msg['type'], msg['channel'], msg['number']):
                continue
            if m.connection and m.connection != msg.get('connection'):
                continue
            if m.type == 'note' and msg['value'] == 0:
                continue  # fire notes on press only, not release
            data = {**m.data, 'value': msg['value'], 'mapping': m.name}
            logger.info(f"MIDI '{m.name}' -> {m.command} {data}")
            try:
                self._pm.hook.showrunner_command(command_name=m.command, data=data)
            except Exception:
                logger.exception(f'MIDI dispatch of {m.command} failed')

    def get_status(self) -> dict:
        return {
            'show_id': self.show_id,
            'connections': [
                {
                    'name': c.name,
                    'direction': c.direction,
                    'enabled': c.enabled,
                    'open': self.is_open(c.direction, c.name),
                    'error': self.errors.get((c.direction, c.name)),
                }
                for c in self.settings.connections
            ],
        }


_midi = MidiManager()


# ---------------------------------------------------------------------------
# Settings persistence
# ---------------------------------------------------------------------------


def _toml_settings(config: Any) -> dict:
    cfg = dict(config.plugins.settings.get('showmidi', {})) if config is not None else {}
    legacy = cfg.pop('port_name', None)
    if legacy and not cfg.get('connections'):
        cfg['connections'] = [{'name': 'default', 'port': str(legacy), 'direction': 'out'}]
    return cfg


def _load_settings(config: Any, show_id: int | None) -> ShowMidiSettings:
    """Per-show DB settings, else show.toml ``[plugins.showmidi]``, else empty."""
    if show_id is not None:
        try:
            with get_db().session() as s:
                row = s.exec(
                    select(Config).where(Config.show_id == show_id, Config.key == SETTINGS_KEY)
                ).first()
            if row and row.value:
                return ShowMidiSettings.model_validate_json(row.value)
        except Exception as e:
            logger.warning(f'MIDI settings for show {show_id} unreadable, using show.toml: {e}')
    try:
        return ShowMidiSettings(**_toml_settings(config))
    except ValidationError as e:
        logger.warning(f'Invalid [plugins.showmidi] in show.toml: {e}')
        return ShowMidiSettings()


def _save_settings(show_id: int, settings: ShowMidiSettings) -> None:
    with get_db().session() as s:
        row = s.exec(
            select(Config).where(Config.show_id == show_id, Config.key == SETTINGS_KEY)
        ).first()
        if row is None:
            row = Config(show_id=show_id, key=SETTINGS_KEY)
        row.value = settings.model_dump_json()
        s.add(row)
        s.commit()


def activate(show_id: int | None) -> None:
    """Load and apply the settings for ``show_id``."""
    _midi.show_id = show_id
    _midi.apply(_load_settings(_midi.config, show_id))


def _first_show_id() -> int | None:
    """Same default as the UI header: first show by name."""
    try:
        with get_db().session() as s:
            return s.exec(select(Show.id).order_by(Show.name)).first()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# HTTP routes
# ---------------------------------------------------------------------------


def _require_rtmidi() -> None:
    if not HAS_RTMIDI:
        raise HTTPException(503, 'python-rtmidi not installed')


def _out(connection: str | None) -> str:
    connection = connection or _midi.default_output()
    if not connection:
        raise HTTPException(400, 'no output connection configured')
    return connection


@router.get('/')
async def index():
    return {'plugin': 'ShowMidi', 'rtmidi_installed': HAS_RTMIDI, **_midi.get_status()}


@router.get('/ports')
async def list_ports():
    _require_rtmidi()
    return {'outputs': _midi.ports('out'), 'inputs': _midi.ports('in')}


@router.post('/note')
async def post_note(
    channel: int = Query(1, ge=1, le=16),
    note: int = Query(60, ge=0, le=127),
    velocity: int = Query(127, ge=0, le=127),
    connection: str | None = None,
):
    _require_rtmidi()
    try:
        _midi.send_note(_out(connection), channel, note, velocity)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {'sent': 'note', 'channel': channel, 'note': note, 'velocity': velocity}


@router.post('/cc')
async def post_cc(
    channel: int = Query(1, ge=1, le=16),
    control: int = Query(7, ge=0, le=127),
    value: int = Query(0, ge=0, le=127),
    connection: str | None = None,
):
    _require_rtmidi()
    try:
        _midi.send_cc(_out(connection), channel, control, value)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {'sent': 'cc', 'channel': channel, 'control': control, 'value': value}


@router.post('/send/{mapping_name}')
async def post_mapping(mapping_name: str):
    _require_rtmidi()
    try:
        _midi.send_mapping(mapping_name)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {'sent': 'mapping', 'name': mapping_name}


@router.post('/panic')
async def post_panic():
    _require_rtmidi()
    _midi.panic()
    return {'sent': 'panic'}


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------


class ShowMidiPlugin(ShowRunnerPlugin):
    """Persistent MIDI I/O. Commands: ``midi.note``, ``midi.cc``, ``midi.send``, ``midi.panic``."""

    @showrunner.hookimpl
    def showrunner_register(self) -> dict:
        return {
            'name': 'ShowMidi',
            'description': 'Persistent MIDI connections with incoming/outgoing mappings',
            'version': '0.2.0',
        }

    @showrunner.hookimpl
    def showrunner_startup(self, app) -> None:
        super().showrunner_startup(app)
        if not HAS_RTMIDI:
            logger.warning('python-rtmidi not installed: MIDI disabled (uv sync --extra midi)')
            return
        _midi._pm = app.pm
        _midi.config = getattr(app, 'config', None)
        activate(getattr(_midi.config, 'current_show', None) or _first_show_id())
        _build_page()

    @showrunner.hookimpl
    def showrunner_shutdown(self, app) -> None:
        _midi.close_all()
        super().showrunner_shutdown(app)

    @showrunner.hookimpl
    def showrunner_config_changed(self, config, previous_config) -> None:
        if not HAS_RTMIDI:
            return
        _midi.config = config
        settings = _load_settings(config, _midi.show_id)
        if settings != _midi.settings:  # don't bounce open ports on unrelated edits
            _midi.apply(settings)

    @showrunner.hookimpl
    def showrunner_command(self, command_name: str, data: dict | None) -> None:
        if not HAS_RTMIDI or not command_name.startswith('midi.'):
            return
        d = data or {}
        try:
            if command_name == 'midi.note':
                _midi.send_note(
                    d.get('connection') or _midi.default_output(),
                    int(d.get('channel', 1)), int(d.get('note', 60)), int(d.get('velocity', 127)),
                )
            elif command_name == 'midi.cc':
                _midi.send_cc(
                    d.get('connection') or _midi.default_output(),
                    int(d.get('channel', 1)), int(d.get('control', 7)), int(d.get('value', 0)),
                )
            elif command_name == 'midi.send':
                _midi.send_mapping(d['mapping'])
            elif command_name == 'midi.panic':
                _midi.panic()
        except Exception as e:
            logger.warning(f'{command_name} failed: {e}')

    @showrunner.hookimpl
    def showrunner_get_routes(self):
        return router

    @showrunner.hookimpl
    def showrunner_get_commands(self) -> list:
        return []

    @showrunner.hookimpl
    def showrunner_get_nav(self):
        return {'label': 'MIDI', 'path': '/midi', 'icon': 'piano', 'order': 30}

    @showrunner.hookimpl
    def showrunner_get_status(self):
        if not HAS_RTMIDI:
            return None
        enabled = [c for c in _midi.settings.connections if c.enabled]
        if not enabled:
            return {'icon': 'piano', 'tooltip': 'MIDI: no connections', 'color': 'grey',
                    'path': '/midi'}
        n = sum(_midi.is_open(c.direction, c.name) for c in enabled)
        color = 'green' if n == len(enabled) else 'amber' if n else 'red'
        return {'icon': 'piano', 'tooltip': f'MIDI: {n}/{len(enabled)} connected',
                'color': color, 'path': '/midi'}


# ---------------------------------------------------------------------------
# /midi page
# ---------------------------------------------------------------------------


def _build_page() -> None:
    from nicegui import ui

    from showrunner.ui import _current_show_id, header

    @ui.page('/midi')
    def midi_page():
        ui.dark_mode(True)
        header()

        show_id = _current_show_id()
        if show_id != _midi.show_id:
            activate(show_id)  # follow the show selected in the header
        draft = _midi.settings.model_dump()
        status_labels: dict[tuple[str, str], Any] = {}
        learning: dict = {'mapping': None}

        def setter(d: dict, key: str, cast=None, then=None):
            def handler(e):
                d[key] = cast(e.value) if cast and e.value is not None else e.value
                if then is not None:
                    then.refresh()
            return handler

        def port_options(direction: str, current: str) -> list[str]:
            try:
                names = [
                    stable_port_name(p) for p in _midi.ports(direction)
                    if not p.startswith(f'{CLIENT_NAME}:')
                ]
            except Exception as e:
                ui.notify(f'Cannot list MIDI ports: {e}', type='negative')
                names = []
            return list(dict.fromkeys([current, *names] if current else names))

        def save():
            try:
                settings = ShowMidiSettings.model_validate(draft)
            except ValidationError as e:
                err = e.errors()[0]
                ui.notify(f"Invalid: {err['msg']}", type='negative', multi_line=True)
                return
            names = [(c.direction, c.name) for c in settings.connections]
            if len(names) != len(set(names)):
                ui.notify('Connection names must be unique per direction', type='negative')
                return
            if show_id is not None:
                _save_settings(show_id, settings)
            _midi.apply(settings)
            failed = [f'{n}: {err}' for (_, n), err in _midi.errors.items()]
            if failed:
                ui.notify('Saved, but: ' + '; '.join(failed), type='warning', multi_line=True)
            else:
                ui.notify('Saved and connected', type='positive')

        def test(m: dict):
            try:
                mapping = Mapping.model_validate(m)
                if mapping.direction == 'in':
                    if not mapping.command:
                        ui.notify('Set a command first', type='warning')
                        return
                    data = {**mapping.data, 'value': 127, 'mapping': mapping.name}
                    _midi._pm.hook.showrunner_command(command_name=mapping.command, data=data)
                    ui.notify(f'Fired {mapping.command}')
                elif mapping.type == 'note':
                    _midi.send_note(mapping.connection, mapping.channel, mapping.number,
                                    mapping.value or 127)
                    ui.timer(0.5, lambda: _midi.send_note(
                        mapping.connection, mapping.channel, mapping.number, 0), once=True)
                else:
                    _midi.send_cc(mapping.connection, mapping.channel, mapping.number,
                                  mapping.value)
            except (ValidationError, KeyError) as e:
                ui.notify(f'{e} (save first?)', type='negative')

        def learn(m: dict):
            learning['mapping'] = m
            _midi.start_learn(timeout=10)
            ui.notify('Learning: send a note or move a control (10 s)')

        with ui.column().classes('w-full max-w-7xl mx-auto p-4 gap-4'):
            if not HAS_RTMIDI:
                ui.label('python-rtmidi is not installed (uv sync --extra midi)').classes(
                    'text-red text-lg')
                return

            with ui.row().classes('w-full items-center gap-4'):
                ui.label('MIDI').classes('text-h5')
                ui.label(f'show #{show_id}' if show_id else 'no show: changes not persisted'
                         ).classes('text-grey')
                ui.space()
                ui.switch('Auto-reconnect', value=draft['auto_reconnect'],
                          on_change=setter(draft, 'auto_reconnect'))
                ui.button('Save & apply', icon='save', on_click=save).props('color=primary')
                ui.button('PANIC', icon='front_hand', on_click=lambda: (
                    _midi.panic(), ui.notify('Panic sent'))).props('color=negative')

            # -- connections --
            with ui.card().classes('w-full'):
                with ui.row().classes('w-full items-center'):
                    ui.label('Connections').classes('text-h6')
                    ui.space()
                    ui.button(icon='refresh', on_click=lambda: connections.refresh()).props(
                        'flat round').tooltip('Rescan ports')

                @ui.refreshable
                def connections():
                    status_labels.clear()
                    for c in draft['connections']:
                        with ui.row().classes('w-full items-center gap-2 no-wrap'):
                            ui.input('Name', value=c['name'],
                                     on_change=setter(c, 'name')).classes('w-32')
                            ui.select({'out': 'Output', 'in': 'Input'}, label='Direction',
                                      value=c['direction'],
                                      on_change=setter(c, 'direction', then=connections)
                                      ).classes('w-28')
                            ui.select(port_options(c['direction'], c['port']), label='Port',
                                      value=c['port'] or None, with_input=True,
                                      new_value_mode='add-unique',
                                      on_change=setter(c, 'port', cast=str)
                                      ).classes('grow').props(
                                          'disable' if c['virtual'] else '')
                            ui.checkbox('Virtual', value=c['virtual'],
                                        on_change=setter(c, 'virtual', then=connections)
                                        ).tooltip(f'Create a {CLIENT_NAME}:<name> port instead')
                            ui.switch('On', value=c['enabled'], on_change=setter(c, 'enabled'))
                            status_labels[(c['direction'], c['name'])] = ui.label().classes(
                                'w-48 text-sm')
                            ui.button(icon='delete', on_click=lambda c=c: (
                                draft['connections'].remove(c), connections.refresh(),
                                mappings.refresh())).props('flat round dense')
                    ui.button('Add connection', icon='add', on_click=lambda: (
                        draft['connections'].append({
                            'name': f"midi{len(draft['connections']) + 1}", 'port': '',
                            'direction': 'out', 'virtual': False, 'enabled': True}),
                        connections.refresh(), mappings.refresh())).props('flat')

                connections()

            # -- mappings --
            with ui.card().classes('w-full'):
                ui.label('Mappings').classes('text-h6')
                ui.label('Send: named messages for midi.send / cues.  '
                         'Receive: incoming Note On or CC fires a command.'
                         ).classes('text-sm text-grey')

                @ui.refreshable
                def mappings():
                    for m in draft['mappings']:
                        incoming = m['direction'] == 'in'
                        names = [c['name'] for c in draft['connections']
                                 if c['direction'] == m['direction']]
                        conn_opts = {'': 'Any input'} if incoming else {}
                        conn_opts |= {n: n for n in names}
                        conn_opts.setdefault(m['connection'], m['connection'] or '(none)')
                        with ui.row().classes('w-full items-center gap-2 no-wrap'):
                            ui.input('Name', value=m['name'],
                                     on_change=setter(m, 'name')).classes('w-32')
                            ui.select({'out': 'Send', 'in': 'Receive'}, label='Direction',
                                      value=m['direction'],
                                      on_change=setter(m, 'direction', then=mappings)
                                      ).classes('w-28')
                            ui.select(conn_opts, label='Connection', value=m['connection'],
                                      on_change=setter(m, 'connection')).classes('w-32')
                            ui.select({'note': 'Note', 'cc': 'CC'}, label='Type', value=m['type'],
                                      on_change=setter(m, 'type')).classes('w-20')
                            ui.number('Ch', value=m['channel'], min=1, max=16, precision=0,
                                      on_change=setter(m, 'channel', int)).classes('w-16')
                            ui.number('Number', value=m['number'], min=0, max=127, precision=0,
                                      on_change=setter(m, 'number', int)).classes('w-20')
                            if incoming:
                                ui.input('Command', value=m['command'],
                                         autocomplete=MIDI_COMMANDS,
                                         on_change=setter(m, 'command')).classes('w-36')
                                ui.input('Data (JSON)',
                                         value=json.dumps(m['data']) if m['data'] else '',
                                         validation={'Invalid JSON': _valid_json},
                                         on_change=lambda e, m=m: _set_json(m, e.value)
                                         ).classes('grow')
                                ui.button('Learn', icon='hearing',
                                          on_click=lambda m=m: learn(m)).props('flat dense')
                            else:
                                ui.number('Value', value=m['value'], min=0, max=127,
                                          precision=0, on_change=setter(m, 'value', int)
                                          ).classes('w-20')
                                ui.space()
                            ui.button('Test', icon='play_arrow',
                                      on_click=lambda m=m: test(m)).props('flat dense')
                            ui.button(icon='delete', on_click=lambda m=m: (
                                draft['mappings'].remove(m), mappings.refresh())
                                ).props('flat round dense')
                    with ui.row():
                        for direction, label in (('out', 'Add send'), ('in', 'Add receive')):
                            ui.button(label, icon='add', on_click=lambda d=direction: (
                                draft['mappings'].append({
                                    'name': f"map{len(draft['mappings']) + 1}",
                                    'direction': d, 'connection': '', 'type': 'cc',
                                    'channel': 1, 'number': 0, 'value': 127,
                                    'command': '', 'data': {}}),
                                mappings.refresh())).props('flat')

                mappings()

            # -- monitor --
            with ui.card().classes('w-full'):
                ui.label('Incoming monitor').classes('text-h6')
                log = ui.log(max_lines=200).classes('w-full h-48 font-mono text-xs')

        last = {'seq': 0}

        def tick():
            for e in _midi.monitor.copy():  # deque.copy is atomic vs. the rtmidi thread
                if e['seq'] > last['seq']:
                    last['seq'] = e['seq']
                    log.push(f"{e['connection']:<12} {e['type'].upper():<4} ch{e['channel']:<2} "
                             f"#{e['number']:<3} = {e['value']}")
            if learning['mapping'] is not None and _midi.learned:
                msg, m = _midi.learned, learning['mapping']
                _midi.learned, learning['mapping'] = None, None
                m.update(type=msg['type'], channel=msg['channel'], number=msg['number'],
                         connection=msg['connection'])
                mappings.refresh()
                ui.notify(f"Learned {msg['type'].upper()} ch{msg['channel']} #{msg['number']}")
            for (direction, name), label in status_labels.items():
                err = _midi.errors.get((direction, name))
                if _midi.is_open(direction, name):
                    label.set_text('● connected')
                    label.classes(replace='w-48 text-sm text-green')
                else:
                    label.set_text(f'○ {err or "not applied"}')
                    label.classes(replace='w-48 text-sm text-red' if err else 'w-48 text-sm')

        ui.timer(0.2, tick)


def _valid_json(text: str) -> bool:
    try:
        return not text or isinstance(json.loads(text), dict)
    except ValueError:
        return False


def _set_json(m: dict, text: str) -> None:
    if _valid_json(text):
        m['data'] = json.loads(text) if text else {}
