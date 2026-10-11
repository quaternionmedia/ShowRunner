"""Shared pytest fixtures for the ShowRunner test suite.

Import these by name in any test file — pytest discovers them automatically.

Example::

    def test_something(db):
        with db.session() as s:
            s.add(Show(name="Hamlet"))
            s.commit()
"""

import json
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from showrunner.database import ShowDatabase


@pytest.fixture()
def db(tmp_path):
    """Provide a fresh, isolated ShowDatabase for each test.

    The database file lives in pytest's ``tmp_path`` directory, which is
    unique per test, so tests never share state.
    """
    database = ShowDatabase(tmp_path / "test.db")
    database.create_schema()
    yield database
    database.close()


# ---------------------------------------------------------------------------
# Fake members of the performing family, for ShowFamily's deliveries
# ---------------------------------------------------------------------------

# The ports a fake member may take, as FIRST-LAST. Unset, the OS picks one.
PORTS_ENV = "SHOWRUNNER_TEST_PORTS"


def _candidate_ports() -> list[int]:
    span = os.environ.get(PORTS_ENV, "")
    if not span:
        return [0]
    first, _, last = span.partition("-")
    return list(range(int(first), int(last or first) + 1))


class _Server(ThreadingHTTPServer):
    # Not SO_REUSEADDR: on Windows it would let this bind a port somebody is
    # already listening on, and the test would talk to them.
    allow_reuse_address = False
    daemon_threads = True


class FakeMember:
    """A family member on a port of its own: ``POST /family/cue`` and ``GET /family/state``.

    It answers ``status`` (200, or 202 for work that finishes later) for an
    action whose keys are all in ``understands``, and 422 with an error for
    anything else, after ``delay`` seconds. Every body it is sent is kept in
    ``received``, in arrival order.
    """

    def __init__(self, name: str, understands=("clock",), status: int = 200,
                 delay: float = 0.0) -> None:
        self.name = name
        self.understands = set(understands)
        self.status = status
        self.delay = delay
        self.received: list[dict] = []
        self.cue = None
        self.server = self._bind()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def _bind(self) -> _Server:
        member = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def answer(self, status: int, body: dict) -> None:
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                time.sleep(member.delay)
                member.received.append(body)
                action = body.get("action")
                if self.path != "/family/cue":
                    return self.answer(404, {"ok": False, "error": "no such route"})
                if not isinstance(action, dict) or not set(action) <= member.understands:
                    return self.answer(422, {"ok": False, "error": f"no action {action!r}"})
                member.cue = body["cue"]
                state = {"seen": len(member.received)}
                return self.answer(member.status, {"ok": True, "state": state})

            def do_GET(self):  # noqa: N802
                return self.answer(200, {"member": member.name, "cue": member.cue,
                                         "state": {"seen": len(member.received)}})

        for port in _candidate_ports():
            try:
                return _Server(("127.0.0.1", port), Handler)
            except OSError:
                continue
        raise RuntimeError(f"no free port in {os.environ.get(PORTS_ENV)}")

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture()
def fake_member():
    """Start fake family members on demand; all are stopped after the test."""
    started: list[FakeMember] = []

    def start(name: str = "showstopper", **kwargs) -> FakeMember:
        member = FakeMember(name, **kwargs)
        started.append(member)
        return member

    yield start
    for member in started:
        member.close()


@pytest.fixture()
def dead_url() -> str:
    """The address of a member that is down: a port that was free a moment ago.

    Taken from the top of the range, so a fake member started after it, which
    takes the first free port from the bottom, does not land on it.
    """
    for port in reversed(_candidate_ports()):
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return f"http://127.0.0.1:{probe.getsockname()[1]}"
    raise RuntimeError(f"no free port in {os.environ.get(PORTS_ENV)}")
