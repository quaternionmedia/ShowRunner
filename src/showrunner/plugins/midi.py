"""ShowMidi - persistent MIDI connections with incoming/outgoing mappings.

Built on :mod:`showrunner.connection`: a *connection* is a named client with any mix of
in/out *ports*. Each port is its own rtmidi client (named ``ShowRunner``) that either
attaches to a system port (substring match) or creates a *virtual* port that other apps
can patch to. Outgoing mappings are named messages (``midi.send``); incoming mappings
turn a received Note On / CC into a ``showrunner_command`` call.

Settings live per show in the DB ``Config`` table (key ``midi.settings``), falling back
to ``[plugins.showmidi]`` in show.toml. Edit them at ``/midi``.

show.toml fallback::

    [plugins.showmidi]
    auto_reconnect = true

    [[plugins.showmidi.connections]]
    name = "piano"
    ports = [
      { name = "out", direction = "out", port = "Digital Piano MIDI 1" },
      { name = "in",  direction = "in",  port = "Digital Piano MIDI 1" },
    ]

    [[plugins.showmidi.mappings]]
    name = "mute"
    connection = "piano"
    type = "cc"
    number = 7
    value = 0

The older flat form (``{name, port, direction}`` entries, ``port_name = "..."``) is still read.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from loguru import logger
from pydantic import Field, model_validator

try:
    import rtmidi

    HAS_RTMIDI = True
except ImportError:
    HAS_RTMIDI = False

from showrunner.connection import (
    Connection,
    ConnectionManager,
    ConnectionPlugin,
    ConnectionSettings,
    Mapping,
    Port,
    common_routes,
)
from showrunner.connection_ui import ConnectionPage

router = APIRouter(prefix='/midi', tags=['ShowMidi'])
CLIENT_NAME = 'ShowRunner'
MIDI_COMMANDS = ['midi.send', 'midi.panic', 'midi.note', 'midi.cc']


# ---------------------------------------------------------------------------
# Settings models
# ---------------------------------------------------------------------------


class MidiPort(Port):
    port: str = ''  # substring of the system port name; unused when virtual
    virtual: bool = False

    @model_validator(mode='after')
    def _port_required(self):
        if not self.virtual and not self.port.strip():
            raise ValueError('a MIDI port needs a system port name (or virtual = true)')
        return self


class MidiConnection(Connection):
    ports: list[MidiPort] = Field(..., min_length=1)


class MidiMapping(Mapping):
    type: Literal['note', 'cc'] = 'cc'
    channel: int = Field(1, ge=1, le=16)
    number: int = Field(0, ge=0, le=127)
    value: int = Field(127, ge=0, le=127)  # outgoing velocity / CC value


class ShowMidiSettings(ConnectionSettings):
    connections: list[MidiConnection] = Field(default_factory=list)
    mappings: list[MidiMapping] = Field(default_factory=list)

    @classmethod
    def upgrade(cls, data: dict) -> dict:
        legacy = data.pop('port_name', None)  # shorthand: one output called "default"
        if legacy and not data.get('connections'):
            data['connections'] = [{'name': 'default', 'port': str(legacy), 'direction': 'out'}]
        return data


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


class MidiManager(ConnectionManager):
    protocol = 'midi'
    settings_cls = ShowMidiSettings
    settings_key = 'midi.settings'
    toml_section = 'showmidi'

    def __init__(self) -> None:
        super().__init__()
        # one rtmidi object (= one ALSA client) per port, reused across reconnects
        self._clients: dict[tuple[str, str, str, bool], Any] = {}  # (conn, port, dir, virtual)
        self._probe: dict[str, Any] = {}  # direction -> client used for listing ports
        # clients whose close_port() failed: never reused, closed or deleted again
        self._retired: list[Any] = []

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

    @staticmethod
    def _label(conn: Connection, port: MidiPort) -> str:
        """Name shown in other apps: the connection name, plus the port when ambiguous."""
        same = sum(p.direction == port.direction for p in conn.ports)
        return conn.name if same == 1 else f'{conn.name}-{port.name}'

    # -- ConnectionManager hooks --------------------------------------------

    def open(self, conn: Connection, port: MidiPort) -> Any:
        key = (conn.name, port.name, port.direction, port.virtual)
        if key not in self._clients:
            cls = rtmidi.MidiOut if port.direction == 'out' else rtmidi.MidiIn
            self._clients[key] = cls(name=CLIENT_NAME)
        client = self._clients[key]
        label = self._label(conn, port)
        if port.virtual:
            if not client.is_port_open():  # rtmidi can't reopen a virtual port; keep it open
                client.open_virtual_port(label)
        else:
            idx = self._find_port_index(port.port, port.direction)
            if idx is None:
                raise ValueError(f"no {port.direction}put port matching '{port.port}'")
            client.open_port(idx, name=label)
        if port.direction == 'in':
            client.set_callback(self._on_midi, (conn.name, port.name))  # close_port cancels it
        return client

    def _detach(self, ckey: tuple[str, str, str, bool]) -> bool:
        """``close_port()`` the client for ``ckey``; retire it if that fails.

        python-rtmidi 1.5.8 bundles RtMidi 5.0.0, whose WinMM input ``closePort()`` fails
        once the device has vanished and leaves the object half-closed: reopening it is a
        silent no-op, and closing it again (``close_port``, ``delete`` or the destructor)
        corrupts the heap. Such a client is dropped from ``_clients``, so ``open`` makes a
        fresh one, and kept referenced so its destructor never runs.
        """
        client = self._clients.get(ckey)
        if client is None:
            return False
        try:
            client.close_port()
        except Exception as e:
            self._retired.append(self._clients.pop(ckey))
            logger.warning(f'MIDI {ckey[0]}/{ckey[1]}: close failed, client retired ({e})')
            return False
        return True

    def close(self, key: tuple[str, str], handle: Any) -> None:
        # real ports detach (open() reattaches; the device may have changed); virtual stay open
        for ckey, client in list(self._clients.items()):
            if client is handle and not ckey[3]:
                self._detach(ckey)

    def release(self, settings: ConnectionSettings | None) -> None:
        keep = (
            {(c.name, p.name, p.direction, p.virtual) for c, p in settings.active_ports()}
            if settings
            else set()
        )
        for ckey in list(self._clients):
            if ckey not in keep and self._detach(ckey):
                try:
                    self._clients.pop(ckey).delete()
                except Exception:
                    pass

    def present(self, conn: Connection, port: MidiPort) -> bool:
        return port.virtual or self._find_port_index(port.port, port.direction) is not None

    def matches(self, mapping: MidiMapping, msg: dict) -> bool:
        if (mapping.type, mapping.channel, mapping.number) != (
            msg['type'],
            msg['channel'],
            msg['number'],
        ):
            return False
        return not (mapping.type == 'note' and msg['value'] == 0)  # press only, not release

    def payload(self, msg: dict) -> dict:
        return {'value': msg['value']}

    def describe(self, msg: dict) -> str:
        t, ch, n, v = msg['type'].upper(), msg['channel'], msg['number'], msg['value']
        return f'{t:<4} ch{ch:<2} #{n:<3} = {v}'

    def _on_midi(self, event: tuple, data: tuple[str, str]) -> None:
        """rtmidi callback (runs on rtmidi's thread)."""
        msg = decode(event[0])
        if msg is not None:
            self.on_message(data[0], data[1], msg)

    # -- output -------------------------------------------------------------

    def _send(self, connection: str, msg: list[int], port: str | None = None) -> None:
        self.fan_out(connection, lambda key, h: h.send_message(msg), port)

    def send_note(self, connection: str, channel: int, note: int, velocity: int, port=None):
        # shortcut: no duration_ms auto Note Off, add a threading.Timer when cues need timed notes
        kind = 0x90 if velocity > 0 else 0x80
        self._send(connection, [kind | (channel - 1) & 0x0F, note & 0x7F, velocity & 0x7F], port)

    def send_cc(self, connection: str, channel: int, control: int, value: int, port=None):
        self._send(connection, [0xB0 | (channel - 1) & 0x0F, control & 0x7F, value & 0x7F], port)

    def send_mapping(self, name: str) -> None:
        m = next(
            (m for m in self.settings.mappings if m.name == name and m.direction == 'out'),
            None,
        )
        if m is None:
            raise KeyError(f"no outgoing mapping named '{name}'")
        send = self.send_note if m.type == 'note' else self.send_cc
        send(m.connection, m.channel, m.number, m.value, m.port or None)

    def panic(self) -> None:
        """All Notes Off (CC 123) + All Sound Off (CC 120) on every channel and output."""
        with self._lock:
            for c, p in self.settings.active_ports():
                out = self.handles.get((c.name, p.name))
                if p.direction == 'out' and out is not None:
                    for ch in range(16):
                        out.send_message([0xB0 | ch, 123, 0])
                        out.send_message([0xB0 | ch, 120, 0])
        logger.info('MIDI panic sent')


_midi = MidiManager()
common_routes(router, _midi, 'ShowMidi', lambda: HAS_RTMIDI)


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
    port: str | None = None,
):
    _require_rtmidi()
    try:
        _midi.send_note(_out(connection), channel, note, velocity, port)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {'sent': 'note', 'channel': channel, 'note': note, 'velocity': velocity}


@router.post('/cc')
async def post_cc(
    channel: int = Query(1, ge=1, le=16),
    control: int = Query(7, ge=0, le=127),
    value: int = Query(0, ge=0, le=127),
    connection: str | None = None,
    port: str | None = None,
):
    _require_rtmidi()
    try:
        _midi.send_cc(_out(connection), channel, control, value, port)
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
# /midi page
# ---------------------------------------------------------------------------


class MidiPage(ConnectionPage):
    path = '/midi'
    title = 'MIDI'
    mapping_cls = MidiMapping
    commands = MIDI_COMMANDS
    port_defaults = {'port': '', 'virtual': False}
    mapping_defaults = {'type': 'cc', 'channel': 1, 'number': 0, 'value': 127}

    def _port_options(self, direction: str, current: str) -> list[str]:
        from nicegui import ui

        try:
            names = [
                stable_port_name(p)
                for p in _midi.ports(direction)
                if not p.startswith(f'{CLIENT_NAME}:')
            ]
        except Exception as e:
            ui.notify(f'Cannot list MIDI ports: {e}', type='negative')
            names = []
        return list(dict.fromkeys([current, *names] if current else names))

    def port_fields(self, p: dict, refresh) -> None:
        from nicegui import ui

        ui.select(self._port_options(p['direction'], p['port']), label='System port',
                  value=p['port'] or None, with_input=True, new_value_mode='add-unique',
                  on_change=lambda e: p.update(port=e.value or '')
                  ).classes('grow').props('disable' if p['virtual'] else '')
        ui.checkbox('Virtual', value=p['virtual'],
                    on_change=lambda e: (p.update(virtual=e.value), refresh.refresh())
                    ).tooltip(f'Create a {CLIENT_NAME}:<name> port instead')

    def mapping_fields(self, mp: dict, incoming: bool, refresh) -> None:
        from nicegui import ui

        def set_(key, cast=None):
            return lambda e: mp.update({key: cast(e.value) if cast and e.value is not None
                                        else e.value})

        ui.select({'note': 'Note', 'cc': 'CC'}, label='Type', value=mp['type'],
                  on_change=set_('type')).classes('w-20')
        ui.number('Ch', value=mp['channel'], min=1, max=16, precision=0,
                  on_change=set_('channel', int)).classes('w-16')
        ui.number('Number', value=mp['number'], min=0, max=127, precision=0,
                  on_change=set_('number', int)).classes('w-20')
        if not incoming:
            ui.number('Value', value=mp['value'], min=0, max=127, precision=0,
                      on_change=set_('value', int)).classes('w-20')

    def test_out(self, m: MidiMapping) -> None:
        from nicegui import ui

        port = m.port or None
        if m.type == 'note':
            _midi.send_note(m.connection, m.channel, m.number, m.value or 127, port)
            ui.timer(0.5, lambda: _midi.send_note(m.connection, m.channel, m.number, 0, port),
                     once=True)
        else:
            _midi.send_cc(m.connection, m.channel, m.number, m.value, port)

    def test_payload(self, m: MidiMapping) -> dict:
        return {'value': 127}

    def apply_learned(self, mp: dict, msg: dict) -> None:
        mp.update(type=msg['type'], channel=msg['channel'], number=msg['number'],
                  connection=msg['connection'])

    def toolbar(self) -> None:
        from nicegui import ui

        ui.button('PANIC', icon='front_hand',
                  on_click=lambda: (_midi.panic(), ui.notify('Panic sent'))).props('color=negative')


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------


class ShowMidiPlugin(ConnectionPlugin):
    """Persistent MIDI I/O. Commands: ``midi.note``, ``midi.cc``, ``midi.send``, ``midi.panic``."""

    protocol = 'midi'
    label = 'ShowMidi'
    description = 'Persistent MIDI connections with incoming/outgoing mappings'
    version = '0.2.0'
    nav_label = 'MIDI'
    icon = 'piano'
    nav_order = 30
    manager = _midi
    router = router
    page_cls = MidiPage

    def backend_available(self) -> bool:
        return HAS_RTMIDI

    def install_hint(self) -> str:
        return 'python-rtmidi is not installed (uv sync --extra midi)'

    def handle_command(self, command_name: str, d: dict) -> None:
        conn = d.get('connection') or _midi.default_output()
        port = d.get('port')
        if command_name == 'midi.note':
            _midi.send_note(conn, int(d.get('channel', 1)), int(d.get('note', 60)),
                            int(d.get('velocity', 127)), port)
        elif command_name == 'midi.cc':
            _midi.send_cc(conn, int(d.get('channel', 1)), int(d.get('control', 7)),
                          int(d.get('value', 0)), port)
        elif command_name == 'midi.send':
            _midi.send_mapping(d['mapping'])
        elif command_name == 'midi.panic':
            _midi.panic()
