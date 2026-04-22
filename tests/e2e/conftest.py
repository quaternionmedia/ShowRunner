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

import subprocess
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

    proc = subprocess.Popen(
        ["uv", "run", "--extra", "cli", "sr", "start", "--config", str(toml_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
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

    yield base_url

    proc.terminate()
    proc.wait(timeout=5)
