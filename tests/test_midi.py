"""Tests for the ShowMidi plugin, using a fake rtmidi module (no hardware)."""

import types
from unittest.mock import MagicMock

import pytest

from showrunner.models import Show
from showrunner.plugins import midi
from showrunner.plugins.midi import (
    Connection,
    Mapping,
    ShowMidiPlugin,
    ShowMidiSettings,
    _load_settings,
    _midi,
    _save_settings,
    decode,
    stable_port_name,
)

PORTS = {
    'out': ['Midi Through:Midi Through Port-0 14:0', 'Digital Piano:Digital Piano MIDI 1 16:0'],
    'in': ['Midi Through:Midi Through Port-0 14:0', 'Digital Piano:Digital Piano MIDI 1 16:0'],
}


class FakePort:
    direction = 'out'

    def __init__(self, name='RtMidi'):
        self.client = name
        self.opened = None  # (index, name) or ('virtual', name)
        self.messages = []
        self.callback = None

    def get_ports(self):
        return list(PORTS[self.direction])

    def open_port(self, port=0, name=None):
        self.opened = (port, name)

    def open_virtual_port(self, name=None):
        self.opened = ('virtual', name)

    def close_port(self):
        self.opened = None
        self.callback = None  # real rtmidi cancels the callback too

    def is_port_open(self):
        return self.opened is not None

    def delete(self):
        self.deleted = True

    def send_message(self, msg):
        self.messages.append(msg)

    def set_callback(self, func, data=None):
        self.callback = (func, data)

    def receive(self, msg):
        """Simulate rtmidi delivering ``msg`` on its thread."""
        func, data = self.callback
        func((msg, 0.0), data)


class FakeIn(FakePort):
    direction = 'in'


@pytest.fixture(autouse=True)
def fake_rtmidi(monkeypatch):
    monkeypatch.setattr(midi, 'rtmidi', types.SimpleNamespace(MidiOut=FakePort, MidiIn=FakeIn),
                        raising=False)
    monkeypatch.setattr(midi, 'HAS_RTMIDI', True)
    _midi._probe.clear()
    _midi._pm = MagicMock()
    yield
    _midi.close_all()
    _midi.settings = ShowMidiSettings()
    _midi.monitor.clear()
    _midi.learn_until = 0.0
    _midi._pm = None


def settings(**kw):
    kw.setdefault('auto_reconnect', False)
    kw.setdefault('connections', [
        {'name': 'piano', 'port': 'Digital Piano', 'direction': 'out'},
        {'name': 'keys', 'port': 'digital piano midi 1', 'direction': 'in'},
    ])
    return ShowMidiSettings(**kw)


# -- models / helpers ---------------------------------------------------------


def test_models_validate():
    with pytest.raises(ValueError):
        Connection(name='x', port='p', direction='sideways')
    with pytest.raises(ValueError):
        Connection(name='x', port='')  # needs a port unless virtual
    assert Connection(name='x', virtual=True).port == ''
    with pytest.raises(ValueError):
        Mapping(name='m', channel=17)


def test_decode():
    assert decode([0x91, 60, 100]) == {'type': 'note', 'channel': 2, 'number': 60, 'value': 100}
    assert decode([0x80, 60, 64])['value'] == 0
    assert decode([0xB0, 7, 50]) == {'type': 'cc', 'channel': 1, 'number': 7, 'value': 50}
    assert decode([0xF8]) is None
    assert decode([0xC0, 5, 0]) is None


def test_stable_port_name():
    assert stable_port_name('Digital Piano:Digital Piano MIDI 1 16:0') == \
        'Digital Piano:Digital Piano MIDI 1'


# -- connections --------------------------------------------------------------


def test_apply_opens_matching_ports_once():
    _midi.apply(settings())
    out, inp = _midi._outputs['piano'], _midi._inputs['keys']
    assert out.opened == (1, 'piano')
    assert inp.opened == (1, 'keys')
    _midi.send_note('piano', 1, 60, 100)
    _midi.send_note('piano', 1, 60, 0)
    assert _midi._outputs['piano'] is out  # same open port, not reopened per message
    assert out.messages == [[0x90, 60, 100], [0x80, 60, 0]]


def test_apply_reports_missing_port_and_disabled():
    _midi.apply(settings(connections=[
        {'name': 'gone', 'port': 'Nope', 'direction': 'out'},
        {'name': 'off', 'port': 'Digital Piano', 'enabled': False},
    ]))
    assert _midi._outputs == {}
    assert 'Nope' in _midi.errors[('out', 'gone')]
    assert ('out', 'off') not in _midi.errors


def test_apply_reopens_on_port_change():
    _midi.apply(settings())
    _midi.apply(settings(connections=[{'name': 'piano', 'port': 'Midi Through'}]))
    assert _midi._outputs['piano'].opened == (0, 'piano')


def test_virtual_port():
    _midi.apply(settings(connections=[{'name': 'sr', 'virtual': True, 'direction': 'in'}]))
    assert _midi._inputs['sr'].opened == ('virtual', 'sr')


def test_refresh_drops_and_reopens(monkeypatch):
    _midi.apply(settings())
    monkeypatch.setitem(PORTS, 'out', ['Midi Through:Midi Through Port-0 14:0',
                                       'ShowRunner:piano 130:0'])  # own port must not match
    _midi.refresh()
    assert 'piano' not in _midi._outputs
    assert _midi.errors[('out', 'piano')] == 'device disconnected'
    monkeypatch.setitem(PORTS, 'out', ['Digital Piano:Digital Piano MIDI 1 20:0'])
    _midi.refresh()
    assert _midi._outputs['piano'].opened == (0, 'piano')
    assert ('out', 'piano') not in _midi.errors


def test_reconnect_and_apply_reuse_client(monkeypatch):
    """One rtmidi object (= one ALSA client) per connection, never a second one."""
    _midi.apply(settings(connections=[
        {'name': 'piano', 'port': 'Digital Piano'},
        {'name': 'keys', 'port': 'Digital Piano', 'direction': 'in'},
        {'name': 'sr', 'virtual': True},
    ]))
    out, keys, sr = _midi._outputs['piano'], _midi._inputs['keys'], _midi._outputs['sr']
    monkeypatch.setitem(PORTS, 'out', [])
    monkeypatch.setitem(PORTS, 'in', [])
    _midi.refresh()
    monkeypatch.setitem(PORTS, 'out', ['Digital Piano:Digital Piano MIDI 1 20:0'])
    monkeypatch.setitem(PORTS, 'in', ['Digital Piano:Digital Piano MIDI 1 20:0'])
    _midi.refresh()
    assert _midi._outputs['piano'] is out and out.opened == (0, 'piano')
    assert _midi._inputs['keys'] is keys and keys.callback is not None  # callback restored
    _midi.apply(_midi.settings)  # Save & apply
    assert _midi._outputs['piano'] is out and _midi._outputs['sr'] is sr
    assert sr.opened == ('virtual', 'sr')  # virtual port never closed
    _midi.apply(settings(connections=[{'name': 'piano', 'port': 'Digital Piano'}]))
    assert keys.deleted and sr.deleted and len(_midi._clients) == 1


def test_reconnect_thread_toggles():
    _midi.apply(settings(auto_reconnect=True, reconnect_interval=0.01))
    assert _midi._thread.is_alive()
    _midi.apply(settings(auto_reconnect=False))
    assert _midi._thread is None


# -- output -------------------------------------------------------------------


def test_cc_mapping_and_panic():
    _midi.apply(settings(mappings=[
        {'name': 'mute', 'connection': 'piano', 'type': 'cc', 'channel': 2, 'number': 7,
         'value': 0},
    ]))
    out = _midi._outputs['piano']
    _midi.send_mapping('mute')
    assert out.messages == [[0xB1, 7, 0]]
    with pytest.raises(KeyError):
        _midi.send_mapping('nope')
    out.messages.clear()
    _midi.panic()
    assert len(out.messages) == 32
    assert [0xBF, 123, 0] in out.messages and [0xBF, 120, 0] in out.messages


def test_send_unopened_raises():
    with pytest.raises(KeyError):
        _midi.send_cc('piano', 1, 7, 0)


def test_plugin_commands_default_to_first_output():
    _midi.apply(settings(mappings=[{'name': 'mute', 'connection': 'piano', 'number': 7}]))
    out = _midi._outputs['piano']
    p = ShowMidiPlugin()
    p.showrunner_command('midi.note', {'note': 62, 'velocity': 90, 'channel': 3})
    p.showrunner_command('midi.cc', {'control': 1, 'value': 5})
    p.showrunner_command('midi.send', {'mapping': 'mute'})
    p.showrunner_command('other.thing', {})
    assert out.messages == [[0x92, 62, 90], [0xB0, 1, 5], [0xB0, 7, 127]]


# -- input --------------------------------------------------------------------


def test_incoming_dispatch_and_monitor():
    _midi.apply(settings(mappings=[
        {'name': 'go', 'direction': 'in', 'type': 'note', 'number': 60,
         'command': 'midi.panic', 'data': {'x': 1}},
        {'name': 'other-input', 'direction': 'in', 'connection': 'elsewhere', 'type': 'note',
         'number': 60, 'command': 'never'},
    ]))
    keys = _midi._inputs['keys']
    keys.receive([0x90, 60, 100])
    keys.receive([0x80, 60, 0])  # release: monitored, not dispatched
    hook = _midi._pm.hook.showrunner_command
    hook.assert_called_once_with(
        command_name='midi.panic', data={'x': 1, 'value': 100, 'mapping': 'go'})
    assert [m['value'] for m in _midi.monitor] == [100, 0]
    assert _midi.monitor[-1]['connection'] == 'keys'


def test_learn_captures_instead_of_dispatching():
    _midi.apply(settings(mappings=[
        {'name': 'vol', 'direction': 'in', 'type': 'cc', 'number': 7, 'command': 'midi.panic'},
    ]))
    _midi.start_learn()
    _midi._inputs['keys'].receive([0xB0, 7, 50])
    assert _midi.learned == {'type': 'cc', 'channel': 1, 'number': 7, 'value': 50,
                             'connection': 'keys'}
    _midi._pm.hook.showrunner_command.assert_not_called()
    _midi._inputs['keys'].receive([0xB0, 7, 51])  # learn is one-shot
    _midi._pm.hook.showrunner_command.assert_called_once()


# -- settings persistence -----------------------------------------------------


def _config(showmidi):
    return types.SimpleNamespace(plugins=types.SimpleNamespace(settings={'showmidi': showmidi}))


def test_toml_legacy_port_name():
    s = _load_settings(_config({'port_name': 'Digital Piano MIDI 1'}), None)
    assert s.connections == [Connection(name='default', port='Digital Piano MIDI 1')]


def test_db_overrides_toml(db, monkeypatch):
    monkeypatch.setattr(midi, 'get_db', lambda: db)
    with db.session() as s:
        s.add(Show(name='Hamlet'))
        s.commit()
    toml = _config({'port_name': 'Midi Through'})
    assert _load_settings(toml, 1).connections[0].name == 'default'
    _save_settings(1, settings())
    _save_settings(1, settings(auto_reconnect=True))  # update, not duplicate
    assert _load_settings(toml, 1) == settings(auto_reconnect=True)
    assert midi._first_show_id() == 1


def test_status():
    p = ShowMidiPlugin()
    assert p.showrunner_get_status()['color'] == 'grey'
    _midi.apply(settings())
    assert p.showrunner_get_status()['color'] == 'green'
    _midi.apply(settings(connections=[
        {'name': 'piano', 'port': 'Digital Piano'}, {'name': 'gone', 'port': 'Nope'}]))
    assert p.showrunner_get_status()['color'] == 'amber'
