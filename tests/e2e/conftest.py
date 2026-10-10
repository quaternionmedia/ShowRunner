"""Fixtures for Playwright end-to-end tests against a live ShowRunner server.

Setup::

    uv sync --group e2e
    uv run playwright install chromium

Run::

    uv run pytest tests/e2e

The server binds 127.0.0.1 on a free port so it never meets a developer's own
instance on 8000. Set ``SHOWRUNNER_E2E_PORT`` to pin the port instead.
"""

import os
import socket
import subprocess
import sys
import textwrap
import time

import httpx
import pytest


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


@pytest.fixture(scope='session')
def live_server(tmp_path_factory):
    """Base URL of an ``sr start`` subprocess with its own config and database.

    Yields once ``/openapi.json`` answers 200, with one show and a cue list of three
    cues seeded. The server's output goes to ``server.log`` beside the database, and
    a server that exits or never answers fails the session rather than skipping it.
    """
    tmp = tmp_path_factory.mktemp('e2e')
    port = int(os.environ.get('SHOWRUNNER_E2E_PORT') or _free_port())
    config = tmp / 'show.toml'
    config.write_text(textwrap.dedent(f"""
        [server]
        host = "127.0.0.1"
        port = {port}

        [database]
        path = "{(tmp / 'e2e.db').as_posix()}"
    """))
    log = (tmp / 'server.log').open('w')
    # The CLI module through this interpreter, not `sr` from PATH (which may be
    # another install) or `uv run` (which can hold sr.exe locked on Windows).
    proc = subprocess.Popen(
        [sys.executable, '-m', 'showrunner.cli', 'start', '--config', str(config)],
        cwd=tmp, stdout=log, stderr=subprocess.STDOUT,
    )
    base_url = f'http://127.0.0.1:{port}'
    try:
        deadline = time.monotonic() + 30
        while True:
            if proc.poll() is not None:
                pytest.fail(f'sr start exited with {proc.returncode}; see {log.name}')
            try:
                if httpx.get(f'{base_url}/openapi.json', timeout=1).status_code == 200:
                    break
            except httpx.TransportError:
                pass
            if time.monotonic() > deadline:
                pytest.fail(f'{base_url} did not answer within 30 s; see {log.name}')
            time.sleep(0.25)
        show = httpx.post(f'{base_url}/db/shows', params={'name': 'E2E Show'})
        show.raise_for_status()
        # A cue list for the programmer page to fire. Each cue has a layer: /script
        # cannot render a cue without one.
        cue_list = httpx.post(f"{base_url}/db/shows/{show.json()['id']}/cue-lists",
                              json={'name': 'Main'})
        cue_list.raise_for_status()
        for number, name in enumerate(['Intro', 'Scene 1', 'Scene 2'], start=1):
            httpx.post(f"{base_url}/db/cue-lists/{cue_list.json()['id']}/cues",
                       json={'number': number, 'name': name, 'layer': 'Stage'}
                       ).raise_for_status()
        yield base_url
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        log.close()
