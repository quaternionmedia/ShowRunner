"""Shared fixtures for Playwright end-to-end tests.

These tests require a live ShowRunner server.  The ``live_server`` fixture
starts a subprocess server with a temporary database, waits until the OpenAPI
docs endpoint responds, then tears it down after the test session.

Prerequisites::

    uv sync --group e2e
    playwright install chromium

Run with::

    uv run pytest tests/e2e/ -v
"""

import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

import httpx
import pytest


@pytest.fixture(scope="session")
def live_server(tmp_path_factory):
    """Start a ShowRunner server in a subprocess for the test session.

    Writes a temporary ``show.toml`` so the server uses an isolated database
    and an unprivileged port (8765), avoiding conflicts with a developer's
    running instance.  Waits up to 12 s for the server to accept requests,
    then yields the base URL.  Tears down after the session.

    The server is launched via the venv's ``sr`` entry-point script directly
    (not through ``uv run``) to avoid the Windows file-lock on ``sr.exe``
    that occurs when ``uv`` tries to reinstall the package while a previous
    ``uv run`` subprocess still holds the executable open.
    """
    tmp = Path(tempfile.mkdtemp(prefix="sr_e2e_"))
    db_path = tmp / "e2e.db"
    toml_path = tmp / "show.toml"
    port = 8765
    toml_path.write_text(
        textwrap.dedent(f"""
            [server]
            host = "127.0.0.1"
            port = {port}

            [database]
            path = "{db_path.as_posix()}"
        """).lstrip()
    )

    # Prefer the venv's sr script; fall back to running the CLI module via
    # sys.executable so we never go through `uv run` (which locks sr.exe on
    # Windows while syncing).
    sr_cmd = shutil.which("sr")
    if sr_cmd:
        cmd = [sr_cmd, "start", "--config", str(toml_path)]
    else:
        cmd = [sys.executable, "-m", "showrunner.cli", "start", "--config", str(toml_path)]

    # DEVNULL instead of PIPE: NiceGUI's WebSocket logs burst on each page
    # connection; a PIPE buffer that fills on Windows blocks the server process,
    # causing all subsequent page navigations to time out.
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f"http://localhost:{port}"

    # Poll /docs (OpenAPI UI) as the readiness probe — it exists on every
    # ShowRunner instance regardless of which plugins are loaded.
    deadline = time.time() + 12
    while time.time() < deadline:
        try:
            resp = httpx.get(f"{base_url}/docs", timeout=1)
            if resp.status_code < 500:
                break
        except Exception:
            time.sleep(0.25)
    else:
        proc.terminate()
        pytest.fail("ShowRunner server did not start within 12 seconds")

    # Seed a minimal show + cue list so the Programmer page has something to fire.
    _seed_show(base_url)

    yield base_url

    proc.terminate()
    proc.wait(timeout=5)


def _seed_show(base_url: str) -> None:
    """POST a minimal show and cue list to the running server via its REST API.

    API conventions (from ShowDB plugin):
    - POST /db/shows          name as query param
    - POST /db/shows/{id}/cue-lists  body: CueListCreate (JSON)
    - POST /db/cue-lists/{id}/cues   body: CueCreate (JSON)
    """
    try:
        resp = httpx.post(f"{base_url}/db/shows", params={"name": "E2E Test Show"}, timeout=5)
        resp.raise_for_status()
        show_id = resp.json()["id"]

        resp = httpx.post(
            f"{base_url}/db/shows/{show_id}/cue-lists",
            json={"name": "Main"},
            timeout=5,
        )
        resp.raise_for_status()
        cl_id = resp.json()["id"]

        for i, name in enumerate(["Intro", "Scene 1", "Scene 2"], start=1):
            httpx.post(
                f"{base_url}/db/cue-lists/{cl_id}/cues",
                json={"number": i, "name": name},
                timeout=5,
            )
    except Exception as exc:
        print(f"[live_server] seed warning: {exc}")
