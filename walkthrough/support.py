"""What the runtime-bound walkthrough pages need: a live server, a browser, a shutter.

These helpers start the real server and the real browser and record what the
page saw. They assert nothing themselves; the page makes its assertions in the
same run that takes the picture.
"""

from __future__ import annotations

import atexit
import json
import os
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import urllib.error
import urllib.request
from pathlib import Path

# `sr check` points the shutter elsewhere, so a check of somebody else's server
# writes no picture into the checkout.
MEDIA = Path(os.environ.get('SHOWRUNNER_WALKTHROUGH_SHOTS') or Path(__file__).resolve().parent / 'media')
STARTUP_TIMEOUT = 40.0  # a cold start on Windows imports FastAPI and NiceGUI first

# Everything to shut down however the run ends: doctest stops at the first
# failing example, so a page's own teardown at the bottom may never run.
_LEAVING: list = []


@atexit.register
def _teardown() -> None:
    while _LEAVING:
        try:
            _LEAVING.pop()()
        except Exception:
            pass


class Unreachable(RuntimeError):
    """The runtime a runtime-bound page needs is not there. Raised, never skipped."""


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


class LiveApp:
    """``sr start`` on a free port with its own config and database.

    A port of its own rather than 8000, where a developer's own server usually
    runs: a page that measured that one would test whatever code it was running.

    With ``SHOWRUNNER_WALKTHROUGH_URL`` set -- ``sr check --url`` sets it -- the
    page attaches to the server already answering there, and neither starts
    nor stops one.
    """

    def __init__(self) -> None:
        self.attached = os.environ.get('SHOWRUNNER_WALKTHROUGH_URL', '').rstrip('/')
        self.process: subprocess.Popen | None = None
        if self.attached:
            self.base = self.attached
            return
        self.dir = Path(tempfile.mkdtemp(prefix='showrunner-walkthrough-'))
        self.port = _free_port()
        self.base = f'http://127.0.0.1:{self.port}'

    def start(self) -> LiveApp:
        if self.attached:
            if not self._answering():
                raise Unreachable(f'{self.base} does not answer')
            return self
        config = self.dir / 'show.toml'
        config.write_text(textwrap.dedent(f"""
            [server]
            host = "127.0.0.1"
            port = {self.port}

            [database]
            path = "{(self.dir / 'show.db').as_posix()}"
        """))
        self.log = self.dir / 'server.log'
        self.process = subprocess.Popen(
            [sys.executable, '-m', 'showrunner.cli', 'start', '--config', str(config)],
            cwd=self.dir, stdout=self.log.open('w'), stderr=subprocess.STDOUT,
        )
        _LEAVING.append(self.stop)
        deadline = time.monotonic() + STARTUP_TIMEOUT
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise Unreachable(f'sr start exited with {self.process.returncode}: {self.log}')
            if self._answering():
                return self
            time.sleep(0.2)
        raise Unreachable(f'{self.base} never answered: {self.log}')

    def _answering(self) -> bool:
        try:
            with urllib.request.urlopen(f'{self.base}/openapi.json', timeout=1) as r:
                return r.status == 200
        except (urllib.error.URLError, OSError):
            return False

    def post(self, path: str) -> dict:
        request = urllib.request.Request(self.base + path, method='POST')
        with urllib.request.urlopen(request, timeout=5) as r:
            return json.load(r)

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()


class Shots:
    """A screenshot library that only records. It never compares and never gates.

    A test that diffs images fails on a font and gets switched off along with the
    assertions beside it, so the regression protection is the page's assertions
    and the picture is their byproduct. What it does check is that the page held
    still for the shot and that it wrote the file it says it wrote.
    """

    def __init__(self, page_slug: str) -> None:
        self.slug = page_slug
        MEDIA.mkdir(parents=True, exist_ok=True)

    def take(self, page, name: str) -> str:
        path = MEDIA / f'{self.slug}-{name}.png'
        # Shoot until two frames in a row match. /script lays itself out again
        # after its last element appears and has no CSS animation left to wait
        # for, so a single shot differed from run to run, and every
        # `uv run pytest` left a modified PNG behind.
        frame = page.screenshot(animations='disabled', caret='hide')
        for _ in range(20):
            page.wait_for_timeout(150)
            previous, frame = frame, page.screenshot(animations='disabled', caret='hide')
            if frame == previous:
                break
        else:
            raise AssertionError(f'{path.name}: the page never stopped changing')
        path.write_bytes(frame)
        if not path.is_file() or path.stat().st_size == 0:
            raise AssertionError(f'screenshot {path.name} was not written')
        return path.name


def chromium():
    """A Chromium that shuts itself down even if the page fails half way."""
    from playwright.sync_api import sync_playwright

    driver = sync_playwright().start()
    _LEAVING.append(driver.stop)
    browser = driver.chromium.launch()
    _LEAVING.append(browser.close)
    browser._driver = driver
    return browser


def release(browser) -> None:
    """Close the browser and its driver now rather than at exit.

    Playwright's sync driver keeps an event loop running in this thread, and an
    async test collected after this page could not start its own until it stops.
    """
    browser.close()
    browser._driver.stop()
