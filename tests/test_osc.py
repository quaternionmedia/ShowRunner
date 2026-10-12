"""Tests for the ShowOSC plugin, using fake pythonosc clients/servers (no sockets)."""

import threading
import time
import types
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from showrunner.plugins import osc
from showrunner.plugins.osc import (
    OscClient,
    OscConnection,
    OscMapping,
    ShowOscPlugin,
    ShowOscSettings,
    _osc,
)


class FakeClient:
    instances = []

    def __init__(self, address, port, **kw):
        self.address, self.port, self.sent, self.closed = address, port, [], False
        FakeClient.instances.append(self)

    def send_message(self, message, value):
        self.sent.append((message, value))

    def close(self):
        self.closed = True


class FakeDispatcher:
    def __init__(self, strict_timing=True):
        self.strict_timing = strict_timing

    def set_default_handler(self, handler, needs_reply_address=False):
        self.handler = handler


class FakeServer:
    instances = []

    def __init__(self, addr, disp):
        if addr[1] == 1:
            raise OSError('Address already in use')
        self.addr, self.dispatcher, self.stopped = addr, disp, False
        FakeServer.instances.append(self)

    def serve_forever(self):
        pass

    def shutdown(self):
        self.stopped = True

    def server_close(self):
        pass

    def receive(self, message, *args, host='10.0.0.2'):
        """Simulate python-osc calling the default handler (needs_reply_address)."""
        self.dispatcher.handler((host, 9999), message, *args)


@pytest.fixture(autouse=True)
def fake_osc(monkeypatch):
    FakeClient.instances, FakeServer.instances = [], []
    monkeypatch.setattr(osc, 'HAS_OSC', True)
    monkeypatch.setattr(osc, 'udp_client', types.SimpleNamespace(SimpleUDPClient=FakeClient),
                        raising=False)
    monkeypatch.setattr(osc, 'tcp_client', types.SimpleNamespace(SimpleTCPClient=FakeClient),
                        raising=False)
    monkeypatch.setattr(osc, 'dispatcher', types.SimpleNamespace(Dispatcher=FakeDispatcher),
                        raising=False)
    monkeypatch.setattr(osc, 'osc_server',
                        types.SimpleNamespace(BlockingOSCUDPServer=FakeServer), raising=False)
    _osc._pm = MagicMock()
    yield
    _osc.close_all()
    _osc.settings = ShowOscSettings()
    _osc.monitor.clear()
    _osc._pm = None


def settings(**kw):
    kw.setdefault('connections', [{'name': 'desk', 'ports': [
        {'name': 'a', 'address': '10.0.0.1', 'port': 8000},
        {'name': 'b', 'address': '10.0.0.2', 'port': 8001, 'protocol': 'tcp'},
        {'name': 'rx', 'direction': 'in', 'address': '0.0.0.0', 'port': 9000},
    ]}])
    return ShowOscSettings(**kw)


def sent(name):
    return _osc.handles[('desk', name)]._client.sent


# -- models / client ----------------------------------------------------------


def test_models_validate():
    assert ShowOscSettings().auto_reconnect is False
    with pytest.raises(ValueError):
        OscConnection(name='x', ports=[{'port': 0}])
    with pytest.raises(ValueError):
        OscConnection(name='x', ports=[{'direction': 'in', 'protocol': 'tcp'}])
    with pytest.raises(ValueError):
        OscMapping(name='m', message='no-slash')
    assert OscMapping(name='m', direction='in').message == ''  # any message


def test_client_sends_falsy_values_and_no_args():
    c = OscClient('h', 1)
    c.send('/a')
    c.send('/b', 0)
    c.send('/c', '')
    c.send('/d', [])
    c.send('/e', [1, 'x'])
    assert c._client.sent == [('/a', None), ('/b', 0), ('/c', ''), ('/d', []), ('/e', [1, 'x'])]
    assert isinstance(c._client, FakeClient) and (c._client.address, c._client.port) == ('h', 1)


# -- connections --------------------------------------------------------------


def test_one_connection_many_ports():
    _osc.apply(settings())
    assert set(_osc.handles) == {('desk', 'a'), ('desk', 'b'), ('desk', 'rx')}
    assert _osc.handles[('desk', 'rx')].addr == ('0.0.0.0', 9000)
    assert _osc.port_counts() == (3, 3)


def test_fan_out_and_port_select():
    _osc.apply(settings())
    _osc.send('desk', '/cue/1/fire', 0)
    assert sent('a') == [('/cue/1/fire', 0)] and sent('b') == [('/cue/1/fire', 0)]
    _osc.send('desk', '/x', [1, 2], port='b')
    assert sent('a') == [('/cue/1/fire', 0)] and sent('b')[-1] == ('/x', [1, 2])
    assert [e['port'] for e in _osc.monitor] == ['a', 'b', 'b']
    assert _osc.monitor[-1]['args'] == [1, 2]


def test_bind_failure_reported_others_still_open():
    _osc.apply(settings(connections=[{'name': 'desk', 'ports': [
        {'name': 'a'}, {'name': 'rx', 'direction': 'in', 'port': 1}]}]))
    assert ('desk', 'a') in _osc.handles
    assert 'in use' in _osc.errors[('desk', 'rx')]


def test_close_stops_server_and_tcp_client():
    _osc.apply(settings())
    tcp, server = _osc.handles[('desk', 'b')], _osc.handles[('desk', 'rx')]
    _osc.apply(ShowOscSettings())
    assert server.stopped and tcp._client is None
    assert any(c.closed for c in FakeClient.instances)


# -- mappings / commands ------------------------------------------------------


def test_default_output_and_plugin_commands():
    _osc.apply(settings(mappings=[
        {'name': 'go', 'message': '/go', 'args': [0, 'x'], 'port': 'a'},
        {'name': 'bare', 'connection': 'desk', 'port': 'a', 'message': '/bare'},
    ]))
    p = ShowOscPlugin()
    p.showrunner_command('osc.send', {'message': '/s', 'args': 5, 'port': 'a'})
    p.showrunner_command('osc.mapping', {'mapping': 'go'})
    p.showrunner_command('osc.mapping', {'mapping': 'bare'})
    p.showrunner_command('other.thing', {})
    assert sent('a') == [('/s', 5), ('/go', [0, 'x']), ('/bare', None)]


def test_incoming_dispatch_glob_and_monitor():
    _osc.apply(settings(mappings=[
        {'name': 'go', 'direction': 'in', 'message': '/go/*', 'command': 'stopper.go',
         'data': {'x': 1}},
        {'name': 'any', 'direction': 'in', 'connection': 'other', 'command': 'never'},
        {'name': 'exact', 'direction': 'in', 'message': '/go', 'command': 'never'},
    ]))
    _osc.handles[('desk', 'rx')].receive('/go/now', 1, 0.5, b'\x00')
    _osc._pm.hook.showrunner_command.assert_called_once_with(
        command_name='stopper.go',
        data={'x': 1, 'message': '/go/now', 'args': [1, 0.5, "b'\\x00'"], 'mapping': 'go'})
    mon = _osc.monitor[-1]
    assert (mon['dir'], mon['connection'], mon['port'], mon['host']) == (
        'in', 'desk', 'rx', '10.0.0.2')


def test_an_incoming_mappings_data_wins_over_the_message():
    """A relay sends what its mapping says; the message only fills what the data leaves out."""
    _osc.apply(settings(mappings=[
        {'name': 'go', 'connection': 'desk', 'port': 'a', 'message': '/eos/cue/1/fire'},
        {'name': 'relay', 'direction': 'in', 'message': '/showrunner/go', 'command': 'osc.send',
         'data': {'connection': 'desk', 'port': 'a', 'message': '/eos/cue/1/fire'}},
        {'name': 'fire-go', 'direction': 'in', 'message': '/showrunner/fire',
         'command': 'osc.mapping', 'data': {'mapping': 'go'}},
    ]))
    plugin = ShowOscPlugin()
    _osc._pm.hook.showrunner_command.side_effect = plugin.showrunner_command
    rx = _osc.handles[('desk', 'rx')]
    rx.receive('/showrunner/go', 1)
    rx.receive('/showrunner/fire')
    assert sent('a') == [('/eos/cue/1/fire', [1]), ('/eos/cue/1/fire', None)]

def test_apply_closes_an_input_without_holding_the_lock_its_dispatch_needs():
    """python-osc joins the message an input is dispatching when it closes; if that
    dispatch sends, it needs the manager's lock, so apply() must not hold it then."""
    s = settings(mappings=[{'name': 'relay', 'direction': 'in', 'message': '/remote/*',
                            'command': 'osc.send', 'data': {'connection': 'desk', 'port': 'a'}}])
    _osc.apply(s)
    plugin, entered, done = ShowOscPlugin(), threading.Event(), threading.Event()

    def slow_command(command_name, data):
        entered.set()
        time.sleep(0.2)
        plugin.showrunner_command(command_name, data)

    _osc._pm.hook.showrunner_command.side_effect = slow_command
    rx = _osc.handles[('desk', 'rx')]
    rx.server_close = lambda: done.wait(5)  # as python-osc's block_on_close join
    threading.Thread(target=lambda: (rx.receive('/remote/go', 1), done.set()), daemon=True).start()
    assert entered.wait(2)
    t = threading.Thread(target=_osc.apply, args=(s,), daemon=True)
    t.start()
    t.join(3)
    assert not t.is_alive(), 'apply() deadlocked against the dispatch it was closing'
    assert FakeServer.instances[-1].dispatcher.strict_timing is False

def test_learn_captures_message():
    _osc.apply(settings())
    _osc.start_learn()
    _osc.handles[('desk', 'rx')].receive('/learned', 3)
    assert _osc.learned['message'] == '/learned'
    _osc._pm.hook.showrunner_command.assert_not_called()


# -- REST ---------------------------------------------------------------------


@pytest.fixture()
def api():
    app = FastAPI()
    app.include_router(osc.router)
    return TestClient(app)


def test_rest_send_and_errors(api, monkeypatch):
    r = api.post('/osc/send', json={'message': '/x'})
    assert r.status_code == 400  # nothing configured
    _osc.apply(settings(mappings=[{'name': 'go', 'message': '/go', 'args': 0}]))
    assert api.post('/osc/send', json={'message': '/x', 'args': 0}).json() == {
        'sent': '/x', 'args': [0]}
    assert sent('a')[-1] == ('/x', 0)
    assert api.post('/osc/send', json={'message': '/x', 'connection': 'nope'}).status_code == 404
    assert api.post('/osc/send', json={'message': 'x'}).status_code == 422
    assert api.post('/osc/send/go').status_code == 200
    assert api.post('/osc/send/nope').status_code == 404
    monkeypatch.setattr(osc, 'HAS_OSC', False)
    assert api.post('/osc/send', json={'message': '/x'}).status_code == 503


def test_rest_status_and_monitor(api):
    _osc.apply(settings())
    _osc.send('desk', '/x')
    body = api.get('/osc/').json()
    assert body['installed'] and len(body['connections'][0]['ports']) == 3
    assert [e['message'] for e in api.get('/osc/monitor').json()] == ['/x', '/x']
    assert api.get('/osc/monitor?after=99').json() == []


def test_plugin_wiring():
    p = ShowOscPlugin()
    assert p.showrunner_get_nav()['path'] == '/osc'
    assert p.showrunner_register()['name'] == 'ShowOSC'
