"""Shared fixtures for Playwright end-to-end tests.

These tests require a live ShowRunner server.  The ``live_server`` fixture
starts a subprocess and waits until the health endpoint responds, then tears
it down after the test session.

Prerequisites::

    uv sync --group e2e
    playwright install chromium
    python examples/setup_intro.py   # seed a test database

Run with::

    uv run pytest tests/e2e/ -v --headed   # or --headless (default)
"""

import subprocess
import time

import httpx
import pytest


@pytest.fixture(scope="session")
def live_server(tmp_path_factory):
    """Start a ShowRunner server in a subprocess for the test session.

    The server uses a temporary database so E2E tests never touch production
    data.  The fixture waits up to 10 seconds for the health check to pass,
    then yields the base URL.
    """
    db_path = tmp_path_factory.mktemp("e2e") / "e2e.db"
    proc = subprocess.Popen(
        ["uv", "run", "sr", "start", "--db", str(db_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    base_url = "http://localhost:8000"

    # Wait for the server to accept connections.
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            resp = httpx.get(f"{base_url}/health", timeout=1)
            if resp.status_code < 500:
                break
        except Exception:
            time.sleep(0.25)
    else:
        proc.terminate()
        pytest.fail("ShowRunner server did not start within 10 seconds")

    yield base_url

    proc.terminate()
    proc.wait(timeout=5)
