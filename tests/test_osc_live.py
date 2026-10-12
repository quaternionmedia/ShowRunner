"""ShowOSC against real python-osc sockets on localhost (skipped without python-osc).

The fakes in test_osc.py cannot show what python-osc's servers and clients do with
threads and timeouts; these tests run the real ones.
"""

import socket
import threading
import time
import types

import pytest

pytest.importorskip('pythonosc')

from pythonosc import udp_client  # noqa: E402

from showrunner.plugins.osc import OscManager, ShowOscSettings  # noqa: E402


def free_port(kind=socket.SOCK_DGRAM) -> int:
    s = socket.socket(socket.AF_INET, kind)
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def wait_for(predicate, timeout=5.0) -> bool:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.01)
    return predicate()


@pytest.fixture()
def manager():
    m = OscManager()
    yield m
    m.close_all()


def on_command(manager, fn) -> None:
    manager._pm = types.SimpleNamespace(hook=types.SimpleNamespace(showrunner_command=fn))


def test_one_input_runs_its_commands_one_at_a_time_in_arrival_order(manager):
    order, active, peak = [], [0], [0]
    lock = threading.Lock()

    def handler(command_name, data):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.002)  # a plugin's handler doing some work
        with lock:
            order.append(data['args'][0])
            active[0] -= 1

    on_command(manager, handler)
    rx = free_port()
    manager.apply(ShowOscSettings(
        connections=[{'name': 'desk', 'ports': [
            {'name': 'rx', 'direction': 'in', 'address': '127.0.0.1', 'port': rx}]}],
        mappings=[{'name': 'go', 'direction': 'in', 'message': '/cue/go',
                   'command': 'stopper.go'}]))
    client = udp_client.SimpleUDPClient('127.0.0.1', rx)
    for i in range(50):
        client.send_message('/cue/go', i)
    assert wait_for(lambda: len(order) == 50)
    assert order == list(range(50))
    assert peak[0] == 1
