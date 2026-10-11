"""Tests for the generic connection layer, using a dummy protocol (no hardware)."""

from unittest.mock import MagicMock

import pytest

from showrunner import connection
from showrunner.connection import (
    Connection,
    ConnectionManager,
    ConnectionPlugin,
    ConnectionSettings,
)


class Dummy(ConnectionManager):
    protocol = 'dummy'
    settings_key = 'dummy.settings'
    toml_section = 'dummy'

    def __init__(self):
        super().__init__()
        self.opened, self.closed, self.gone = [], [], set()

    def open(self, conn, port):
        if port.name in self.gone:
            raise ValueError('device missing')
        self.opened.append((conn.name, port.name))
        return MagicMock()

    def close(self, key, handle):
        self.closed.append(key)

    def present(self, conn, port):
        return port.name not in self.gone

    def matches(self, mapping, msg):
        return mapping.data.get('x') == msg['x'] if 'x' in mapping.data else True

    def describe(self, msg):
        return str(msg)


@pytest.fixture()
def m():
    m = Dummy()
    m._pm = MagicMock()
    yield m
    m.close_all()


def cfg(*conns, **kw):
    kw.setdefault('auto_reconnect', False)
    return ConnectionSettings(connections=list(conns), **kw)


def test_ports_named_and_validated():
    c = Connection(name='c', ports=[{'direction': 'in'}, {'direction': 'in'}, {'name': 'out1'}])
    assert [p.name for p in c.ports] == ['in1', 'in2', 'out1']
    with pytest.raises(ValueError):
        Connection(name='c', ports=[{'name': 'a'}, {'name': 'a'}])
    with pytest.raises(ValueError):
        Connection(name='c', ports=[])
    with pytest.raises(ValueError):
        cfg({'name': 'c', 'ports': [{}]}, {'name': 'c', 'ports': [{}]})


def test_flat_form_folds_into_ports():
    s = cfg({'name': 'a', 'direction': 'out'}, {'name': 'a', 'direction': 'in'}, {'name': 'b'})
    assert [(c.name, [p.name for p in c.ports]) for c in s.connections] == [
        ('a', ['out1', 'in1']),
        ('b', ['out1']),
    ]


def test_apply_closes_removed_and_opens_new(m):
    a = {'name': 'a', 'ports': [{'name': 'p'}, {'name': 'q', 'direction': 'in'}]}
    m.apply(cfg(a))
    assert set(m.handles) == {('a', 'p'), ('a', 'q')}
    m.apply(cfg({'name': 'b', 'ports': [{'name': 'p'}]}))
    assert set(m.handles) == {('b', 'p')}
    assert ('a', 'p') in m.closed  # old handles closed before reopening


def test_errors_and_disabled(m):
    m.gone = {'bad'}
    m.apply(cfg(
        {'name': 'a', 'ports': [{'name': 'bad'}, {'name': 'ok'}, {'name': 'off', 'enabled': False}]},
        {'name': 'b', 'enabled': False, 'ports': [{'name': 'x'}]},
    ))
    assert set(m.handles) == {('a', 'ok')}
    assert m.errors == {('a', 'bad'): 'device missing'}
    assert m.port_counts() == (1, 2)


def test_refresh_drops_and_reopens(m):
    m.apply(cfg({'name': 'a', 'ports': [{'name': 'p'}]}))
    m.gone = {'p'}
    m.refresh()
    assert m.handles == {} and m.errors[('a', 'p')] == 'device disconnected'
    m.gone = set()
    m.refresh()
    assert ('a', 'p') in m.handles and not m.errors


def test_reconnect_thread_toggles(m):
    m.apply(cfg(auto_reconnect=True, reconnect_interval=0.01))
    assert m._thread.is_alive()
    m.apply(cfg(auto_reconnect=False))
    assert m._thread is None


def test_fan_out(m):
    m.apply(cfg({'name': 'a', 'ports': [
        {'name': 'o1'}, {'name': 'o2'}, {'name': 'i', 'direction': 'in'}]}))
    seen = []
    m.fan_out('a', lambda key, h: seen.append(key))
    assert seen == [('a', 'o1'), ('a', 'o2')]  # inputs skipped
    seen.clear()
    m.fan_out('a', lambda key, h: seen.append(key), port='o2')
    assert seen == [('a', 'o2')]
    with pytest.raises(KeyError):
        m.fan_out('nope', lambda k, h: None)
    with pytest.raises(KeyError):
        m.fan_out('a', lambda k, h: None, port='i')

    def one_fails(key, h):
        if key[1] == 'o1':
            raise OSError('boom')

    m.fan_out('a', one_fails)  # partial failure is recorded, not raised
    assert m.errors[('a', 'o1')] == 'boom'
    with pytest.raises(OSError):
        m.fan_out('a', lambda k, h: (_ for _ in ()).throw(OSError('all')))


def test_default_output(m):
    m.apply(cfg({'name': 'in-only', 'ports': [{'direction': 'in'}]},
                {'name': 'off', 'enabled': False, 'ports': [{}]},
                {'name': 'a', 'ports': [{}]}))
    assert m.default_output() == 'a'


def test_dispatch_filters_and_learn(m):
    m.apply(cfg({'name': 'a', 'ports': [{'name': 'i', 'direction': 'in'}]}, mappings=[
        {'name': 'any', 'direction': 'in', 'command': 'c1', 'data': {'k': 1}},
        {'name': 'conn', 'direction': 'in', 'connection': 'z', 'command': 'c2'},
        {'name': 'port', 'direction': 'in', 'port': 'other', 'command': 'c3'},
        {'name': 'send', 'direction': 'out', 'command': 'c4'},
    ]))
    m.on_message('a', 'i', {'x': 1})
    m._pm.hook.showrunner_command.assert_called_once_with(
        command_name='c1', data={'k': 1, 'mapping': 'any'})
    assert [e['dir'] for e in m.monitor] == ['in']
    m.start_learn()
    m.on_message('a', 'i', {'x': 2})
    assert m.learned == {'x': 2, 'connection': 'a', 'port': 'i'}
    assert m._pm.hook.showrunner_command.call_count == 1  # learn swallows the message


def test_events_emitted(m):
    m.emit = MagicMock()
    m.apply(cfg({'name': 'a', 'ports': [{'name': 'i', 'direction': 'in'}]}))
    m.on_message('a', 'i', {'x': 1})
    m.record_out('a', 'o', {'x': 2})
    assert [c.args[0] for c in m.emit.call_args_list] == [
        'dummy.connected', 'dummy.in', 'dummy.out']


def test_status_and_settings_persistence(m, db, monkeypatch):
    monkeypatch.setattr(connection, 'get_db', lambda: db)
    s = cfg({'name': 'a', 'ports': [{'name': 'p'}]})
    m.apply(s)
    st = m.get_status()['connections'][0]
    assert st['ports'] == [
        {'name': 'p', 'direction': 'out', 'enabled': True, 'open': True, 'error': None}]
    m.save_settings(1, s)
    m.save_settings(1, cfg(auto_reconnect=True))  # update, not duplicate
    assert m.load_settings(None, 1) == cfg(auto_reconnect=True)
    assert m.load_settings(None, 99).connections == []  # no row, no config -> defaults


def test_plugin_status_colors(m):
    class P(ConnectionPlugin):
        protocol = 'dummy'
        nav_label = 'Dummy'
        manager = m

    p = P()
    assert p.showrunner_get_status()['color'] == 'grey'
    m.gone = {'bad'}
    m.apply(cfg({'name': 'a', 'ports': [{'name': 'p'}, {'name': 'bad'}]}))
    assert p.showrunner_get_status()['color'] == 'amber'
    m.apply(cfg({'name': 'a', 'ports': [{'name': 'bad'}]}))
    assert p.showrunner_get_status()['color'] == 'red'
    assert p.showrunner_get_nav()['path'] == '/dummy'
