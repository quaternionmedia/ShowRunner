"""Generic connection layer shared by protocol plugins (ShowMidi, ShowOSC).

A *connection* is a named client that owns one or more *ports*. Each port is an
endpoint in one direction (``in`` or ``out``), so a connection can be a single input,
a single output, several outputs (fan-out), several inputs, or any mix.

A protocol plugin subclasses:

* ``Port`` / ``Connection`` / ``Mapping`` / ``ConnectionSettings`` for its config,
* ``ConnectionManager`` (``open``, ``close``, ``matches``, ``payload``, ``describe``),
* ``ConnectionPlugin`` (``handle_command`` plus a few class attributes),
* ``showrunner.connection_ui.ConnectionPage`` for the web page.

This module has no MIDI/OSC imports. Settings are stored per show in the DB ``Config``
table (``manager.settings_key``) and fall back to ``[plugins.<manager.toml_section>]``
in show.toml.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Callable, Literal

from fastapi import APIRouter
from loguru import logger
from pydantic import BaseModel, Field, ValidationError, model_validator
from sqlmodel import select

import showrunner
from showrunner.models import Config, Show
from showrunner.plugin import ShowRunnerPlugin


def get_db():
    # Imported late: showrunner.plugins imports this module while it is being imported.
    from showrunner.plugins.db import get_db as _get_db

    return _get_db()


# ---------------------------------------------------------------------------
# Settings models
# ---------------------------------------------------------------------------


class Port(BaseModel):
    """One endpoint of a connection. Subclasses add protocol fields."""

    name: str = Field('', max_length=64)  # unique per connection; '' = auto (in1, out1, ...)
    direction: Literal['in', 'out'] = 'out'
    enabled: bool = True


class Connection(BaseModel):
    """A named client holding any mix of in/out ports."""

    name: str = Field(..., min_length=1, max_length=64)
    enabled: bool = True
    ports: list[Port] = Field(..., min_length=1)

    @model_validator(mode='after')
    def _name_ports(self):
        named = [p.name for p in self.ports if p.name]
        if len(named) != len(set(named)):
            raise ValueError(f"connection '{self.name}' has duplicate port names")
        used = set(named)
        for p in self.ports:
            if not p.name:
                n = 1
                while f'{p.direction}{n}' in used:
                    n += 1
                p.name = f'{p.direction}{n}'
                used.add(p.name)
        return self


class Mapping(BaseModel):
    """Outgoing: a named message. Incoming: a message pattern that fires ``command``."""

    name: str = Field(..., min_length=1, max_length=64)
    direction: Literal['in', 'out'] = 'out'
    connection: str = ''  # incoming: '' matches any connection
    port: str = ''  # '' = all out ports / any in port
    command: str = ''  # incoming: command to dispatch
    data: dict[str, Any] = Field(default_factory=dict)


class ConnectionSettings(BaseModel):
    auto_reconnect: bool = True
    reconnect_interval: float = Field(2.0, gt=0, lt=60)
    connections: list[Connection] = Field(default_factory=list)
    mappings: list[Mapping] = Field(default_factory=list)

    @classmethod
    def upgrade(cls, data: dict) -> dict:
        """Hook for protocol-specific legacy keys (e.g. MIDI ``port_name``)."""
        return data

    @model_validator(mode='before')
    @classmethod
    def _fold_flat(cls, data):
        """Accept the old flat form ``{name, direction, ...}``: same-name entries become ports."""
        if not isinstance(data, dict):
            return data
        data = cls.upgrade(dict(data))
        out: list = []
        by_name: dict[str, dict] = {}
        for c in data.get('connections') or []:
            if isinstance(c, dict) and 'ports' not in c and 'name' in c:
                port = {k: v for k, v in c.items() if k != 'name'}
                if c['name'] in by_name:
                    by_name[c['name']]['ports'].append(port)
                else:
                    by_name[c['name']] = {'name': c['name'], 'ports': [port]}
                    out.append(by_name[c['name']])
            else:
                out.append(c)
        data['connections'] = out
        return data

    @model_validator(mode='after')
    def _unique_connections(self):
        names = [c.name for c in self.connections]
        if len(names) != len(set(names)):
            raise ValueError('connection names must be unique')
        return self

    def active_ports(self):
        """Yield ``(connection, port)`` for every enabled port of an enabled connection."""
        for c in self.connections:
            if c.enabled:
                for p in c.ports:
                    if p.enabled:
                        yield c, p


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class ConnectionManager:
    """Holds open ports keyed ``(connection, port)`` and dispatches input.

    Subclasses implement ``open`` / ``close`` (and optionally ``present``, ``release``),
    plus ``matches`` / ``payload`` / ``describe`` for incoming mappings. Input callbacks
    call :meth:`on_message`.
    """

    protocol = 'conn'  # log prefix and event/command namespace
    settings_cls: type[ConnectionSettings] = ConnectionSettings
    settings_key = 'conn.settings'  # DB Config key
    toml_section = 'conn'  # [plugins.<toml_section>]

    def __init__(self) -> None:
        self._lock = threading.RLock()  # handles, errors, settings: held by every send
        # apply/refresh/close_all one at a time. They close ports without holding _lock:
        # closing an input waits for the message it is dispatching, which may need _lock.
        self._lifecycle = threading.Lock()
        self.settings = self.settings_cls()
        self.show_id: int | None = None
        self.config: Any = None
        self._pm: Any = None
        self.emit: Callable[..., None] = lambda name, data=None: None
        self.handles: dict[tuple[str, str], Any] = {}
        self.errors: dict[tuple[str, str], str] = {}
        self.monitor: deque = deque(maxlen=200)
        self.seq = 0
        self.learn_until = 0.0
        self.learned: dict | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- protocol hooks -----------------------------------------------------

    def open(self, conn: Connection, port: Port) -> Any:
        """Open the port and return its handle. Raise on failure."""
        raise NotImplementedError

    def close(self, key: tuple[str, str], handle: Any) -> None:
        raise NotImplementedError

    def present(self, conn: Connection, port: Port) -> bool:
        """Is the device behind the port reachable? Used by auto-reconnect."""
        return True

    def release(self, settings: ConnectionSettings | None) -> None:
        """Free resources no longer needed by ``settings`` (None: everything)."""

    def matches(self, mapping: Mapping, msg: dict) -> bool:
        raise NotImplementedError

    def payload(self, msg: dict) -> dict:
        """Extra ``showrunner_command`` data derived from an incoming message."""
        return {}

    def describe(self, msg: dict) -> str:
        raise NotImplementedError

    # -- logging / events ---------------------------------------------------

    def log(self, level: str, key: tuple[str, str], text: str) -> None:
        logger.log(level, f'{self.protocol.upper()} {key[0]}/{key[1]}: {text}')

    def _emit(self, suffix: str, data: dict) -> None:
        try:
            self.emit(f'{self.protocol}.{suffix}', data)
        except Exception:
            logger.exception(f'{self.protocol} event {suffix} failed')

    # -- lifecycle ----------------------------------------------------------

    def is_open(self, connection: str, port: str) -> bool:
        return (connection, port) in self.handles

    def _try_open(self, conn: Connection, port: Port) -> bool:
        key = (conn.name, port.name)
        try:
            self.handles[key] = self.open(conn, port)
        except Exception as e:
            self.errors[key] = str(e)
            self.log('WARNING', key, f'{port.direction} port: {e}')
            return False
        self.errors.pop(key, None)
        self.log('INFO', key, f'{port.direction} port open')
        self._emit('connected', {'connection': conn.name, 'port': port.name})
        return True

    def _close(self, handles: list[tuple[tuple[str, str], Any]]) -> None:
        """Close ``handles``, already taken out of ``self.handles``. Call without ``_lock``."""
        for key, handle in handles:
            try:
                self.close(key, handle)
            except Exception:
                pass

    def _take_all(self) -> list[tuple[tuple[str, str], Any]]:
        with self._lock:
            taken, self.handles = list(self.handles.items()), {}
        return taken

    def apply(self, settings: ConnectionSettings) -> None:
        """Close everything and reopen per ``settings`` (so edits always take effect)."""
        with self._lifecycle:
            self._close(self._take_all())
            self.release(settings)
            with self._lock:
                self.settings = settings
                self.errors = {}
                for conn, port in settings.active_ports():
                    self._try_open(conn, port)
        self._update_thread()

    def refresh(self) -> None:
        """Drop ports whose device vanished; open ports whose device appeared."""
        gone: list[tuple[tuple[str, str], Any]] = []
        with self._lifecycle:
            with self._lock:
                for conn, port in self.settings.active_ports():
                    key = (conn.name, port.name)
                    present = self.present(conn, port)
                    if key in self.handles and not present:
                        gone.append((key, self.handles.pop(key)))
                        self.errors[key] = 'device disconnected'
                        self.log('WARNING', key, 'disconnected')
                        self._emit('disconnected', {'connection': conn.name, 'port': port.name})
                    elif key not in self.handles and present:
                        self._try_open(conn, port)
            self._close(gone)

    def close_all(self) -> None:
        self._stop_thread()
        with self._lifecycle:
            self._close(self._take_all())
            self.release(None)

    def _update_thread(self) -> None:
        if not self.settings.auto_reconnect:
            self._stop_thread()
        elif not (self._thread and self._thread.is_alive()):
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._reconnect_loop, daemon=True, name=f'{self.protocol}-reconnect'
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
                logger.warning(f'{self.protocol.upper()} reconnect: {e}')

    # -- output -------------------------------------------------------------

    def default_output(self) -> str | None:
        return next(
            (
                c.name
                for c in self.settings.connections
                if c.enabled and any(p.enabled and p.direction == 'out' for p in c.ports)
            ),
            None,
        )

    def fan_out(self, connection: str, fn: Callable[[tuple[str, str], Any], None], port=None):
        """Call ``fn(key, handle)`` on every open out port of ``connection`` (or just ``port``).

        A failing port is logged and recorded in ``errors`` without stopping the others;
        if every port fails the first error is raised. ``KeyError`` if nothing is open.
        """
        with self._lock:
            keys = [
                (c.name, p.name)
                for c in self.settings.connections
                if c.name == connection
                for p in c.ports
                if p.direction == 'out' and port in (None, '', p.name)
                if (c.name, p.name) in self.handles
            ]
            if not keys:
                raise KeyError(f"no open output on '{connection}'" + (f"/{port}" if port else ''))
            failures = []
            for key in keys:
                try:
                    fn(key, self.handles[key])
                except Exception as e:
                    self.errors[key] = str(e)
                    self.log('WARNING', key, f'send failed: {e}')
                    failures.append(e)
            if len(failures) == len(keys):
                raise failures[0]

    def record_out(self, connection: str, port: str, msg: dict) -> None:
        """Show an outgoing message in the monitor and emit ``<protocol>.out``."""
        self.seq += 1
        entry = {**msg, 'connection': connection, 'port': port, 'dir': 'out', 'seq': self.seq}
        self.monitor.append(entry)
        self._emit('out', entry)

    # -- input --------------------------------------------------------------

    def start_learn(self, timeout: float = 10.0) -> None:
        """Capture the next incoming message into ``learned`` instead of dispatching it."""
        self.learned = None
        self.learn_until = time.monotonic() + timeout

    def on_message(self, connection: str, port: str, msg: dict) -> None:
        """Entry point for protocol input callbacks (runs on the protocol's thread)."""
        msg = {**msg, 'connection': connection, 'port': port}
        self.seq += 1
        self.monitor.append({**msg, 'dir': 'in', 'seq': self.seq})
        self._emit('in', msg)
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
            if m.connection and m.connection != msg.get('connection'):
                continue
            if m.port and m.port != msg.get('port'):
                continue
            if not self.matches(m, msg):
                continue
            data = {'mapping': m.name, **self.payload(msg), **m.data}
            logger.info(f"{self.protocol.upper()} '{m.name}' -> {m.command} {data}")
            try:
                self._pm.hook.showrunner_command(command_name=m.command, data=data)
            except Exception:
                logger.exception(f'{self.protocol.upper()} dispatch of {m.command} failed')

    # -- status -------------------------------------------------------------

    def get_status(self) -> dict:
        return {
            'show_id': self.show_id,
            'connections': [
                {
                    'name': c.name,
                    'enabled': c.enabled,
                    'ports': [
                        {
                            'name': p.name,
                            'direction': p.direction,
                            'enabled': p.enabled,
                            'open': self.is_open(c.name, p.name),
                            'error': self.errors.get((c.name, p.name)),
                        }
                        for p in c.ports
                    ],
                }
                for c in self.settings.connections
            ],
        }

    def port_counts(self) -> tuple[int, int]:
        """``(open, wanted)`` over all enabled ports."""
        wanted = [(c.name, p.name) for c, p in self.settings.active_ports()]
        return sum(k in self.handles for k in wanted), len(wanted)

    # -- persistence --------------------------------------------------------

    def toml_settings(self, config: Any) -> dict:
        return dict(config.plugins.settings.get(self.toml_section, {})) if config else {}

    def load_settings(self, config: Any, show_id: int | None) -> ConnectionSettings:
        """Per-show DB settings, else show.toml ``[plugins.<section>]``, else defaults."""
        if show_id is not None:
            try:
                with get_db().session() as s:
                    row = s.exec(
                        select(Config).where(
                            Config.show_id == show_id, Config.key == self.settings_key
                        )
                    ).first()
                if row and row.value:
                    return self.settings_cls.model_validate_json(row.value)
            except Exception as e:
                logger.warning(f'Settings {self.settings_key} for show {show_id} unreadable: {e}')
        try:
            return self.settings_cls(**self.toml_settings(config))
        except ValidationError as e:
            logger.warning(f'Invalid [plugins.{self.toml_section}] in show.toml: {e}')
            return self.settings_cls()

    def save_settings(self, show_id: int, settings: ConnectionSettings) -> None:
        with get_db().session() as s:
            row = s.exec(
                select(Config).where(Config.show_id == show_id, Config.key == self.settings_key)
            ).first()
            if row is None:
                row = Config(show_id=show_id, key=self.settings_key)
            row.value = settings.model_dump_json()
            s.add(row)
            s.commit()

    def activate(self, show_id: int | None) -> None:
        """Load and apply the settings for ``show_id``."""
        self.show_id = show_id
        self.apply(self.load_settings(self.config, show_id))


def first_show_id() -> int | None:
    """Same default as the UI header: first show by name."""
    try:
        with get_db().session() as s:
            return s.exec(select(Show.id).order_by(Show.name)).first()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# HTTP + plugin
# ---------------------------------------------------------------------------


def common_routes(
    router: APIRouter, manager: ConnectionManager, name: str, installed: Callable[[], bool]
) -> None:
    """Add ``GET /`` (status) and ``GET /monitor?after=<seq>`` to a protocol router."""

    @router.get('/')
    async def index():
        return {'plugin': name, 'installed': installed(), **manager.get_status()}

    @router.get('/monitor')
    async def monitor(after: int = 0):
        return [e for e in manager.monitor.copy() if e['seq'] > after]


class ConnectionPlugin(ShowRunnerPlugin):
    """Base plugin: lifecycle, config reload, status icon, nav, ``<protocol>.*`` commands."""

    protocol = 'conn'
    label = 'Connection'  # plugin name, e.g. 'ShowMidi'
    description = ''
    version = '0.1.0'
    nav_label = 'Conn'
    icon = 'cable'
    nav_order = 30
    manager: ConnectionManager
    router: APIRouter
    page_cls: Any = None  # a connection_ui.ConnectionPage subclass

    def backend_available(self) -> bool:
        """False when the optional protocol library is missing (plugin becomes a no-op)."""
        return True

    def handle_command(self, command_name: str, data: dict) -> None:
        raise NotImplementedError

    def install_hint(self) -> str:
        return ''

    @showrunner.hookimpl
    def showrunner_register(self) -> dict:
        return {'name': self.label, 'description': self.description, 'version': self.version}

    @showrunner.hookimpl
    def showrunner_startup(self, app) -> None:
        super().showrunner_startup(app)
        if not self.backend_available():
            logger.warning(f'{self.label}: {self.install_hint()} ({self.protocol} disabled)')
            return
        m = self.manager
        m._pm = app.pm
        m.emit = self.emit
        m.config = getattr(app, 'config', None)
        m.activate(getattr(m.config, 'current_show', None) or first_show_id())
        self.page_cls(m, self.backend_available, self.install_hint()).register()

    @showrunner.hookimpl
    def showrunner_shutdown(self, app) -> None:
        self.manager.close_all()
        super().showrunner_shutdown(app)

    @showrunner.hookimpl
    def showrunner_config_changed(self, config, previous_config) -> None:
        if not self.backend_available():
            return
        m = self.manager
        m.config = config
        settings = m.load_settings(config, m.show_id)
        if settings != m.settings:  # don't bounce open ports on unrelated edits
            m.apply(settings)

    @showrunner.hookimpl
    def showrunner_command(self, command_name: str, data: dict | None) -> None:
        if not self.backend_available() or not command_name.startswith(f'{self.protocol}.'):
            return
        try:
            self.handle_command(command_name, data or {})
        except Exception as e:
            logger.warning(f'{command_name} failed: {e}')

    @showrunner.hookimpl
    def showrunner_get_routes(self):
        return self.router

    @showrunner.hookimpl
    def showrunner_get_commands(self) -> list:
        return []

    @showrunner.hookimpl
    def showrunner_get_nav(self):
        return {
            'label': self.nav_label,
            'path': f'/{self.protocol}',
            'icon': self.icon,
            'order': self.nav_order,
        }

    @showrunner.hookimpl
    def showrunner_get_status(self):
        if not self.backend_available():
            return None
        n, total = self.manager.port_counts()
        base = {'icon': self.icon, 'path': f'/{self.protocol}'}
        if not total:
            return {**base, 'tooltip': f'{self.nav_label}: no connections', 'color': 'grey'}
        color = 'green' if n == total else 'amber' if n else 'red'
        return {
            **base,
            'tooltip': f'{self.nav_label}: {n}/{total} ports open',
            'color': color,
        }
