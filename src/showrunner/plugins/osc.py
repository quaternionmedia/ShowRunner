"""ShowOSC - persistent OSC connections with incoming/outgoing mappings.

Built on :mod:`showrunner.connection`: a *connection* is a named client with any mix of
in/out *ports*. An out port sends OSC to ``address:port`` over UDP or TCP; an in port
listens on a UDP ``address:port`` and turns matching messages into ``showrunner_command``
calls. Edit settings at ``/osc``; they live per show in the DB (key ``osc.settings``),
falling back to ``[plugins.showosc]`` in show.toml::

    [[plugins.showosc.connections]]
    name = "console"
    ports = [
      { name = "tx", direction = "out", address = "192.168.1.50", port = 8000 },
      { name = "rx", direction = "in",  address = "0.0.0.0",      port = 9000 },
    ]

    [[plugins.showosc.mappings]]
    name = "go"
    connection = "console"
    message = "/eos/cue/1/fire"

    [[plugins.showosc.mappings]]
    name = "on-go"
    direction = "in"
    message = "/showrunner/go"
    command = "stopper.go"

Console dialects (Eos, QLab ...) are meant to be subclasses of :class:`OscClient`/the plugin.
"""

from __future__ import annotations

import fnmatch
import json
import math
import threading
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

try:
    from pythonosc import dispatcher, osc_server, tcp_client, udp_client

    HAS_OSC = True
except ImportError:
    HAS_OSC = False

from showrunner.connection import (
    Connection,
    ConnectionManager,
    ConnectionPlugin,
    ConnectionSettings,
    Mapping,
    Port,
    common_routes,
)
from showrunner.connection_ui import ConnectionPage, valid_json

router = APIRouter(prefix='/osc', tags=['ShowOSC'])
OSC_COMMANDS = ['osc.send', 'osc.mapping']


# ---------------------------------------------------------------------------
# Settings models
# ---------------------------------------------------------------------------


# An OSC argument is a value JSON can carry with an OSC type; a message takes one or a list.
OscArg = bool | int | float | str
OscArgs = OscArg | list[OscArg] | None


class OscPort(Port):
    address: str = Field('127.0.0.1', min_length=1)  # out: destination host; in: bind address
    port: int = Field(8000, ge=1, le=65535)
    protocol: Literal['udp', 'tcp'] = 'udp'

    @model_validator(mode='after')
    def _no_tcp_in(self):
        # shortcut: receive is UDP only, add an OSC-over-TCP (SLIP/length-framed) server when needed
        if self.direction == 'in' and self.protocol == 'tcp':
            raise ValueError('OSC input supports UDP only')
        return self


class OscConnection(Connection):
    ports: list[OscPort] = Field(..., min_length=1)


class OscMapping(Mapping):
    message: str = ''  # out: OSC address to send; in: glob to match ('' = any message)
    args: OscArgs = None  # out: one value or a list of values; None sends no arguments

    @model_validator(mode='after')
    def _out_needs_address(self):
        if self.direction == 'out' and not self.message.startswith('/'):
            raise ValueError("an outgoing OSC message must start with '/'")
        return self


class ShowOscSettings(ConnectionSettings):
    auto_reconnect: bool = False  # UDP is connectionless; nothing to detect
    connections: list[OscConnection] = Field(default_factory=list)
    mappings: list[OscMapping] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Client (same shape as the QLab-python ``Eos`` class)
# ---------------------------------------------------------------------------


class OscClient:
    """Tiny OSC sender. ``send('/path')`` sends no arguments; ``0`` and ``''`` are real values."""

    timeout = 2.0  # seconds for a TCP connect or send (python-osc's default is 30)

    def __init__(self, address: str = '127.0.0.1', port: int = 8000, tcp: bool = False) -> None:
        self.address, self.port, self.tcp = address, port, tcp
        self._client: Any = None

    def connect(self) -> None:
        if self._client is None:
            if self.tcp:
                self._client = tcp_client.SimpleTCPClient(
                    self.address, self.port, timeout=self.timeout
                )
            else:
                self._client = udp_client.SimpleUDPClient(self.address, self.port)

    def send(self, message: str, value: Any = None) -> None:
        self.connect()
        try:
            self._client.send_message(message, value)  # value None -> no args (`is None`)
        except OSError:
            self.close()  # a TCP peer that went away is reconnected on the next send
            raise

    def close(self) -> None:
        if self._client is not None and self.tcp:
            try:
                self._client.close()
            except OSError:
                pass
        self._client = None


# ---------------------------------------------------------------------------
# OscManager
# ---------------------------------------------------------------------------


def _plain(arg: Any) -> Any:
    """Make an OSC argument JSON-safe for the monitor (blobs, midi tuples, NaN ...)."""
    if isinstance(arg, float) and not math.isfinite(arg):
        return repr(arg)  # 'nan', 'inf', '-inf': JSON has no such numbers
    return arg if isinstance(arg, (bool, int, float, str, type(None))) else repr(arg)


class OscManager(ConnectionManager):
    protocol = 'osc'
    settings_cls = ShowOscSettings
    settings_key = 'osc.settings'
    toml_section = 'showosc'

    # -- ConnectionManager hooks --------------------------------------------

    def open(self, conn: Connection, port: OscPort) -> Any:
        key = (conn.name, port.name)
        if port.direction == 'out':
            client = OscClient(port.address, port.port, port.protocol == 'tcp')
            client.connect()  # TCP fails here (not on first send) if the peer is down
            return client
        # Run a bundle's messages on arrival: strict timing parks the receive thread until
        # the bundle's timetag, and closing the port then waits for it.
        disp = dispatcher.Dispatcher(strict_timing=False)
        disp.set_default_handler(
            lambda host, message, *args: self.on_message(
                key[0],
                key[1],
                {'message': message, 'args': [_plain(a) for a in args], 'host': host[0]},
            ),
            needs_reply_address=True,
        )
        # One thread per in-port, so its messages dispatch one at a time, in arrival order.
        server = osc_server.BlockingOSCUDPServer((port.address, port.port), disp)
        threading.Thread(
            target=server.serve_forever, daemon=True, name=f'osc-in-{key[0]}-{key[1]}'
        ).start()
        return server

    def close(self, key: tuple[str, str], handle: Any) -> None:
        if isinstance(handle, OscClient):
            handle.close()
        else:
            handle.shutdown()
            handle.server_close()

    def matches(self, mapping: OscMapping, msg: dict) -> bool:
        return fnmatch.fnmatchcase(msg['message'], mapping.message or '*')

    def payload(self, msg: dict) -> dict:
        return {'message': msg['message'], 'args': msg['args']}

    def describe(self, msg: dict) -> str:
        return f"{msg['message']} {msg['args']}"

    # -- output -------------------------------------------------------------

    def send(self, connection: str, message: str, args: Any = None, port: str | None = None):
        def one(key, client):
            client.send(message, args)
            self.record_out(key[0], key[1], {'message': message, 'args': _plain_args(args)})

        self.fan_out(connection, one, port)

    def send_mapping(self, name: str) -> None:
        m = next(
            (m for m in self.settings.mappings if m.name == name and m.direction == 'out'),
            None,
        )
        if m is None:
            raise KeyError(f"no outgoing mapping named '{name}'")
        conn = m.connection or self.default_output()
        if not conn:
            raise KeyError('no output connection configured')
        self.send(conn, m.message, m.args, m.port or None)


def _plain_args(args: Any) -> list:
    if args is None:
        return []
    return [_plain(a) for a in (args if isinstance(args, (list, tuple)) else [args])]


_osc = OscManager()
common_routes(router, _osc, 'ShowOSC', lambda: HAS_OSC)


# ---------------------------------------------------------------------------
# HTTP routes
# ---------------------------------------------------------------------------


class OscSend(BaseModel):
    message: str = Field(..., pattern=r'^/')
    args: OscArgs = None
    connection: str | None = None
    port: str | None = None


def _require_osc() -> None:
    if not HAS_OSC:
        raise HTTPException(503, 'python-osc not installed')


@router.post('/send')
async def post_send(body: OscSend):
    _require_osc()
    connection = body.connection or _osc.default_output()
    if not connection:
        raise HTTPException(400, 'no output connection configured')
    try:
        _osc.send(connection, body.message, body.args, body.port)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except Exception as e:  # every port it went to failed
        raise HTTPException(502, str(e))
    return {'sent': body.message, 'args': _plain_args(body.args)}


@router.post('/send/{mapping_name}')
async def post_mapping(mapping_name: str):
    _require_osc()
    try:
        _osc.send_mapping(mapping_name)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(502, str(e))
    return {'sent': 'mapping', 'name': mapping_name}


# ---------------------------------------------------------------------------
# /osc page
# ---------------------------------------------------------------------------


class OscPage(ConnectionPage):
    path = '/osc'
    title = 'OSC'
    mapping_cls = OscMapping
    commands = OSC_COMMANDS
    port_defaults = {'address': '127.0.0.1', 'port': 8000, 'protocol': 'udp'}
    mapping_defaults = {'message': '/', 'args': None}

    def port_fields(self, p: dict, refresh) -> None:
        from nicegui import ui

        ui.input('Address', value=p['address'],
                 on_change=lambda e: p.update(address=e.value)).classes('grow')
        ui.number('Port', value=p['port'], min=1, max=65535, precision=0,
                  on_change=lambda e: p.update(port=int(e.value or 0))).classes('w-24')
        ui.select(['udp', 'tcp'] if p['direction'] == 'out' else ['udp'], label='Protocol',
                  value=p['protocol'] if p['direction'] == 'out' else 'udp',
                  on_change=lambda e: p.update(protocol=e.value)).classes('w-20')

    def mapping_fields(self, mp: dict, incoming: bool, refresh) -> None:
        from nicegui import ui

        ui.input('Message' if not incoming else 'Message (glob)', value=mp['message'],
                 on_change=lambda e: mp.update(message=e.value)).classes('w-48')
        if not incoming:
            ui.input('Args (JSON)', value='' if mp['args'] is None else json.dumps(mp['args']),
                     validation={'Invalid JSON': _valid_any},
                     on_change=lambda e: mp.update(args=_loads(e.value))).classes('w-40')

    def test_out(self, m: OscMapping) -> None:
        conn = m.connection or _osc.default_output()
        if not conn:
            raise KeyError('no output connection configured')
        _osc.send(conn, m.message, m.args, m.port or None)

    def test_payload(self, m: OscMapping) -> dict:
        return {'message': m.message, 'args': []}

    def apply_learned(self, mp: dict, msg: dict) -> None:
        mp.update(connection=msg['connection'], message=msg['message'])

    def extra_cards(self, ui) -> None:
        state = {'connection': _osc.default_output(), 'message': '/', 'args': ''}
        with ui.card().classes('w-full'):
            ui.label('Test sender').classes('text-h6')
            with ui.row().classes('w-full items-center gap-2 no-wrap'):
                ui.select([c.name for c in _osc.settings.connections], label='Connection',
                          value=state['connection'],
                          on_change=lambda e: state.update(connection=e.value)).classes('w-40')
                ui.input('Message', value=state['message'],
                         on_change=lambda e: state.update(message=e.value)).classes('grow')
                ui.input('Args (JSON)', validation={'Invalid JSON': _valid_any},
                         on_change=lambda e: state.update(args=e.value)).classes('w-48')
                ui.button('Send', icon='send', on_click=lambda: self._test_send(ui, state))

    @staticmethod
    def _test_send(ui, s: dict) -> None:
        try:
            _osc.send(s['connection'] or '', s['message'], _loads(s['args']))
        except Exception as e:
            ui.notify(str(e), type='negative')


def _valid_any(text: str) -> bool:
    return valid_json(text, object)


def _loads(text: str) -> Any:
    try:
        return json.loads(text) if text else None
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------


class ShowOscPlugin(ConnectionPlugin):
    """Persistent OSC I/O. Commands: ``osc.send``, ``osc.mapping``."""

    protocol = 'osc'
    label = 'ShowOSC'
    description = 'Persistent OSC connections with incoming/outgoing mappings'
    version = '0.1.0'
    nav_label = 'OSC'
    icon = 'settings_input_antenna'
    nav_order = 31
    manager = _osc
    router = router
    page_cls = OscPage

    def backend_available(self) -> bool:
        return HAS_OSC

    def install_hint(self) -> str:
        return 'python-osc is not installed (uv sync --extra osc)'

    def handle_command(self, command_name: str, d: dict) -> None:
        if command_name == 'osc.send':
            conn = d.get('connection') or _osc.default_output()
            if not conn:
                raise KeyError('no output connection configured')
            _osc.send(conn, d['message'], d.get('args'), d.get('port'))
        elif command_name == 'osc.mapping':
            _osc.send_mapping(d['mapping'])
