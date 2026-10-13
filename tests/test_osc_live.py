"""ShowOSC against real python-osc sockets on localhost (skipped without python-osc).

The fakes in test_osc.py cannot show what python-osc's servers and clients do with
threads and timeouts; these tests run the real ones.
"""

import socket
import struct
import threading
import time
import types

import pytest

pytest.importorskip('pythonosc')

from pythonosc import osc_bundle_builder, osc_message_builder, udp_client  # noqa: E402

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
    # Bounded, so a regression that hangs close_all() fails its test instead of the run.
    t = threading.Thread(target=m.close_all, daemon=True)
    t.start()
    t.join(5)


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


def test_a_bundle_timed_for_later_runs_now_and_does_not_hold_the_port_open(manager):
    """With python-osc's strict timing, a bundle stamped an hour ahead parks the receive
    thread until then, and closing the port waits for it."""
    got = []
    on_command(manager, lambda command_name, data: got.append(data['message']))
    rx = free_port()
    manager.apply(ShowOscSettings(
        connections=[{'name': 'desk', 'ports': [
            {'name': 'rx', 'direction': 'in', 'address': '127.0.0.1', 'port': rx}]}],
        mappings=[{'name': 'go', 'direction': 'in', 'command': 'stopper.go'}]))
    bundle = osc_bundle_builder.OscBundleBuilder(time.time() + 3600)
    bundle.add_content(osc_message_builder.OscMessageBuilder('/later').build())
    udp_client.SimpleUDPClient('127.0.0.1', rx).send(bundle.build())
    assert wait_for(lambda: got == ['/later'], timeout=2)
    t = threading.Thread(target=manager.close_all, daemon=True)
    t.start()
    t.join(3)
    assert not t.is_alive(), 'close_all() is waiting on a timed bundle'


class Peer:
    """A TCP OSC receiver that can drop its connection and come back on the same port."""

    def __init__(self, port: int) -> None:
        self.port, self.data, self.conn = port, b'', None
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(('127.0.0.1', port))
        self.srv.listen()
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        try:
            self.conn, _ = self.srv.accept()
            while chunk := self.conn.recv(1024):
                self.data += chunk
        except OSError:
            pass

    def stop(self) -> None:
        """Reset the connection (linger 0 sends RST) and stop listening."""
        if self.conn is not None:
            self.conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', 1, 0))
            self.conn.close()
        self.srv.close()


def test_a_tcp_peer_that_restarts_is_reached_again_on_the_next_send(manager):
    port = free_port(socket.SOCK_STREAM)
    peer = Peer(port)
    manager.apply(ShowOscSettings(connections=[{'name': 'qlab', 'ports': [
        {'name': 'tx', 'address': '127.0.0.1', 'port': port, 'protocol': 'tcp'}]}]))
    manager.send('qlab', '/first')
    assert wait_for(lambda: b'/first' in peer.data)
    peer.stop()
    with pytest.raises(OSError):
        for _ in range(20):  # the first send after a reset can still be accepted locally
            manager.send('qlab', '/lost')
            time.sleep(0.05)
    peer = Peer(port)
    try:
        manager.send('qlab', '/again')
        assert wait_for(lambda: b'/again' in peer.data)
    finally:
        peer.stop()
