"""Shared NiceGUI page for connection plugins (``/midi``, ``/osc``).

Subclasses set ``path`` / ``title`` / ``mapping_cls`` and override the hooks
(``port_fields``, ``mapping_fields``, ``test_out``, ``apply_learned`` ...). NiceGUI is
imported lazily so the package works without a UI.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from pydantic import ValidationError

from showrunner.connection import ConnectionManager, Mapping


def valid_json(text: str, kind: type = dict) -> bool:
    try:
        return not text or isinstance(json.loads(text), kind)
    except ValueError:
        return False


def set_json(d: dict, key: str, text: str, kind: type = dict) -> None:
    if valid_json(text, kind):
        d[key] = json.loads(text) if text else kind()


class ConnectionPage:
    path = '/conn'
    title = 'Connections'
    mapping_cls: type[Mapping] = Mapping
    commands: list[str] = []  # autocomplete for incoming mapping commands
    port_defaults: dict[str, Any] = {}  # extra keys of a new port
    mapping_defaults: dict[str, Any] = {}  # extra keys of a new mapping

    def __init__(
        self, manager: ConnectionManager, available: Callable[[], bool], install_hint: str = ''
    ) -> None:
        self.manager = manager
        self.available = available
        self.install_hint = install_hint

    # -- hooks --------------------------------------------------------------

    def port_fields(self, p: dict, refresh) -> None:
        """Protocol fields of a port row (inside a ``ui.row``)."""

    def mapping_fields(self, mp: dict, incoming: bool, refresh) -> None:
        """Protocol fields of a mapping row (message, args ...)."""

    def test_out(self, mapping) -> None:
        """Send an outgoing mapping."""
        raise NotImplementedError

    def test_payload(self, mapping) -> dict:
        """Extra data when testing an incoming mapping."""
        return {}

    def apply_learned(self, mp: dict, msg: dict) -> None:
        mp['connection'] = msg['connection']

    def toolbar(self) -> None:
        """Extra toolbar widgets."""

    def extra_cards(self, ui) -> None:
        """Extra cards between the mappings and the monitor."""

    def format(self, e: dict) -> str:
        arrow = '<-' if e.get('dir') == 'in' else '->'
        return f"{arrow} {e['connection']}/{e['port']} {self.manager.describe(e)}"

    # -- helpers ------------------------------------------------------------

    def new_port(self, conn: dict, direction: str) -> dict:
        used = {p['name'] for p in conn['ports']}
        n = 1
        while f'{direction}{n}' in used:
            n += 1
        return {
            'name': f'{direction}{n}',
            'direction': direction,
            'enabled': True,
            **self.port_defaults,
        }

    def new_mapping(self, n: int, direction: str) -> dict:
        return {
            'name': f'map{n}',
            'direction': direction,
            'connection': '',
            'port': '',
            'command': '',
            'data': {},
            **self.mapping_defaults,
        }

    # -- page ---------------------------------------------------------------

    def register(self) -> None:
        from nicegui import ui

        ui.page(self.path)(lambda: self.build())

    def build(self) -> None:
        from nicegui import ui

        from showrunner.ui import _current_show_id, header

        m = self.manager
        ui.dark_mode(True)
        header()

        show_id = _current_show_id()
        if show_id != m.show_id:
            m.activate(show_id)  # follow the show selected in the header
        draft = m.settings.model_dump()
        status_labels: dict[tuple[str, str], Any] = {}
        learning: dict = {'mapping': None}

        def setter(d: dict, key: str, cast=None, then=None):
            def handler(e):
                d[key] = cast(e.value) if cast and e.value is not None else e.value
                if then is not None:
                    then.refresh()

            return handler

        def save():
            try:
                settings = m.settings_cls.model_validate(draft)
            except ValidationError as e:
                err = e.errors()[0]
                ui.notify(f"Invalid: {err['msg']}", type='negative', multi_line=True)
                return
            if show_id is not None:
                m.save_settings(show_id, settings)
            m.apply(settings)
            failed = [f'{c}/{p}: {err}' for (c, p), err in m.errors.items()]
            if failed:
                ui.notify('Saved, but: ' + '; '.join(failed), type='warning', multi_line=True)
            else:
                ui.notify('Saved and connected', type='positive')

        def reconnect():
            m.apply(m.settings)
            connections.refresh()

        def test(mp: dict):
            try:
                mapping = self.mapping_cls.model_validate(mp)
                if mapping.direction == 'out':
                    self.test_out(mapping)
                elif not mapping.command:
                    ui.notify('Set a command first', type='warning')
                else:
                    data = {**mapping.data, **self.test_payload(mapping), 'mapping': mapping.name}
                    m._pm.hook.showrunner_command(command_name=mapping.command, data=data)
                    ui.notify(f'Fired {mapping.command}')
            except (ValidationError, KeyError) as e:
                ui.notify(f'{e} (save first?)', type='negative')
            except Exception as e:
                ui.notify(str(e), type='negative')

        def learn(mp: dict):
            learning['mapping'] = mp
            m.start_learn(timeout=10)
            ui.notify('Learning: send a message to an input (10 s)')

        def add_connection():
            n = len(draft['connections']) + 1
            conn = {'name': f'{m.protocol}{n}', 'enabled': True, 'ports': []}
            conn['ports'].append(self.new_port(conn, 'out'))
            draft['connections'].append(conn)
            connections.refresh()

        def add_port(conn: dict, direction: str):
            conn['ports'].append(self.new_port(conn, direction))
            connections.refresh()

        with ui.column().classes('w-full max-w-7xl mx-auto p-4 gap-4'):
            if not self.available():
                ui.label(self.install_hint).classes('text-red text-lg')
                return

            with ui.row().classes('w-full items-center gap-4'):
                ui.label(self.title).classes('text-h5')
                ui.label(
                    f'show #{show_id}' if show_id else 'no show: changes not persisted'
                ).classes('text-grey')
                ui.space()
                ui.switch(
                    'Auto-reconnect',
                    value=draft['auto_reconnect'],
                    on_change=setter(draft, 'auto_reconnect'),
                )
                ui.button('Save & apply', icon='save', on_click=save).props('color=primary')
                self.toolbar()

            # -- connections --
            with ui.card().classes('w-full'):
                with ui.row().classes('w-full items-center'):
                    ui.label('Connections').classes('text-h6')
                    ui.space()
                    ui.button(icon='refresh', on_click=reconnect).props(
                        'flat round'
                    ).tooltip('Reconnect (re-apply saved settings)')

                @ui.refreshable
                def connections():
                    status_labels.clear()
                    for c in draft['connections']:
                        with ui.card().props('flat bordered').classes('w-full'):
                            with ui.row().classes('w-full items-center gap-2 no-wrap'):
                                ui.input('Connection', value=c['name'],
                                         on_change=setter(c, 'name')).classes('w-40')
                                ui.switch('On', value=c['enabled'],
                                          on_change=setter(c, 'enabled'))
                                ui.space()
                                for d, label in (('in', 'In port'), ('out', 'Out port')):
                                    ui.button(label, icon='add',
                                              on_click=lambda c=c, d=d: add_port(c, d)
                                              ).props('flat dense')
                                ui.button(icon='delete', on_click=lambda c=c: (
                                    draft['connections'].remove(c), connections.refresh(),
                                    mappings.refresh())).props('flat round dense')
                            for p in c['ports']:
                                with ui.row().classes('w-full items-center gap-2 no-wrap pl-4'):
                                    ui.select({'out': 'Output', 'in': 'Input'},
                                              label='Direction', value=p['direction'],
                                              on_change=setter(p, 'direction', then=connections)
                                              ).classes('w-28')
                                    ui.input('Port name', value=p['name'],
                                             on_change=setter(p, 'name')).classes('w-24')
                                    self.port_fields(p, connections)
                                    ui.switch('On', value=p['enabled'],
                                              on_change=setter(p, 'enabled'))
                                    status_labels[(c['name'], p['name'])] = ui.label().classes(
                                        'w-48 text-sm')
                                    ui.button(icon='delete', on_click=lambda c=c, p=p: (
                                        c['ports'].remove(p), connections.refresh(),
                                        mappings.refresh())).props('flat round dense')
                    ui.button('Add connection', icon='add', on_click=add_connection).props('flat')

                connections()

            # -- mappings --
            with ui.card().classes('w-full'):
                ui.label('Mappings').classes('text-h6')
                ui.label('Send: named messages for cues and commands.  '
                         'Receive: an incoming message fires a command.'
                         ).classes('text-sm text-grey')

                @ui.refreshable
                def mappings():
                    for mp in draft['mappings']:
                        incoming = mp['direction'] == 'in'
                        sel = next((c for c in draft['connections']
                                    if c['name'] == mp['connection']), None)
                        conn_opts = {'': 'Any connection'} if incoming else {}
                        conn_opts |= {c['name']: c['name'] for c in draft['connections']
                                      if any(p['direction'] == mp['direction']
                                             for p in c['ports'])}
                        conn_opts.setdefault(mp['connection'], mp['connection'] or '(none)')
                        port_opts = {'': 'Any port' if incoming else 'All ports'}
                        port_opts |= {p['name']: p['name'] for p in (sel['ports'] if sel else [])
                                      if p['direction'] == mp['direction']}
                        port_opts.setdefault(mp['port'], mp['port'])
                        with ui.row().classes('w-full items-center gap-2 no-wrap'):
                            ui.input('Name', value=mp['name'],
                                     on_change=setter(mp, 'name')).classes('w-32')
                            ui.select({'out': 'Send', 'in': 'Receive'}, label='Direction',
                                      value=mp['direction'],
                                      on_change=setter(mp, 'direction', then=mappings)
                                      ).classes('w-28')
                            ui.select(conn_opts, label='Connection', value=mp['connection'],
                                      on_change=setter(mp, 'connection', then=mappings)
                                      ).classes('w-32')
                            ui.select(port_opts, label='Port', value=mp['port'],
                                      on_change=setter(mp, 'port')).classes('w-28')
                            self.mapping_fields(mp, incoming, mappings)
                            if incoming:
                                ui.input('Command', value=mp['command'],
                                         autocomplete=self.commands,
                                         on_change=setter(mp, 'command')).classes('w-36')
                                ui.input('Data (JSON)',
                                         value=json.dumps(mp['data']) if mp['data'] else '',
                                         validation={'Invalid JSON': valid_json},
                                         on_change=lambda e, mp=mp: set_json(mp, 'data', e.value)
                                         ).classes('grow')
                                ui.button('Learn', icon='hearing',
                                          on_click=lambda mp=mp: learn(mp)).props('flat dense')
                            else:
                                ui.space()
                            ui.button('Test', icon='play_arrow',
                                      on_click=lambda mp=mp: test(mp)).props('flat dense')
                            ui.button(icon='delete', on_click=lambda mp=mp: (
                                draft['mappings'].remove(mp), mappings.refresh())
                                ).props('flat round dense')
                    with ui.row():
                        for direction, label in (('out', 'Add send'), ('in', 'Add receive')):
                            ui.button(label, icon='add', on_click=lambda d=direction: (
                                draft['mappings'].append(
                                    self.new_mapping(len(draft['mappings']) + 1, d)),
                                mappings.refresh())).props('flat')

                mappings()

            self.extra_cards(ui)

            # -- monitor --
            with ui.card().classes('w-full'):
                ui.label('Monitor').classes('text-h6')
                log = ui.log(max_lines=200).classes('w-full h-48 font-mono text-xs')

        last = {'seq': 0}

        def tick():
            for e in m.monitor.copy():  # deque.copy is atomic vs. the protocol thread
                if e['seq'] > last['seq']:
                    last['seq'] = e['seq']
                    log.push(self.format(e))
            if learning['mapping'] is not None and m.learned:
                msg, mp = m.learned, learning['mapping']
                m.learned, learning['mapping'] = None, None
                self.apply_learned(mp, msg)
                mappings.refresh()
                ui.notify(f'Learned {m.describe(msg)}')
            for (conn, port), label in status_labels.items():
                err = m.errors.get((conn, port))
                if m.is_open(conn, port):
                    label.set_text('● connected')
                    label.classes(replace='w-48 text-sm text-green')
                else:
                    label.set_text(f'○ {err or "not applied"}')
                    label.classes(replace='w-48 text-sm text-red' if err else 'w-48 text-sm')

        ui.timer(0.2, tick)
