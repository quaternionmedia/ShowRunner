"""ShowFamily - deliver each fired cue's ``family`` block to the apps it names.

A cue's ``notes`` JSON may carry a ``family`` block: one action per member of
the performing family, keyed by the member's name in ``[plugins.family]``::

    {"family": {"showstopper": {"clock": "start"},
                "carlos": {"patch": "Launchpad X: complex"},
                "joe": {"take": {"notes": ["C4", "E4", "G4"], "seconds_per_note": 0.5}},
                "leo": {"song": "Blue Bossa"}}}

Whenever a cue fires -- the programmer's GO, ``POST /programmer/cue/{n}``, or
any path that announces the ``cue.fired`` event -- the hub delivers it:

* a **push** member gets ``POST <base>/family/cue`` with
  ``{"cue": {"number", "name", "fired_at"}, "action": {...}}``;
* a **pull** member's action is stored, and ``GET /family/members/<name>``
  returns it as ``{"cue": {...}, "action": {...}}`` until the next cue that
  names it (``{"cue": null, "action": null}`` before the first).

Delivery runs on worker threads, never on the event loop, one queue per push
member so its cues arrive in the order they fired. Every request has a timeout.
A member that is down, slow or refuses the action is recorded, never raised:
``GET /family/`` lists each delivery's outcome -- member, HTTP status (``null``
when nothing answered), and milliseconds from the cue firing to the member's
answer, or to a pull member's first read.

Configure in show.toml (``sr serve --family NAME=URL --family-pull NAME``
writes the same table)::

    [plugins.family]
    pull = ["leo"]
    timeout = 5.0            # seconds per delivery; optional

    [plugins.family.members]
    showstopper = "http://127.0.0.1:18411"
    carlos = "http://127.0.0.1:18420"
    joe = "http://127.0.0.1:18431"
"""

from __future__ import annotations

import threading
import time
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import wait as wait_for
from copy import deepcopy
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Response
from loguru import logger

import showrunner

router = APIRouter(prefix="/family", tags=["ShowFamily"])

# The event the programmer announces on every fire; any other path that fires a
# cue announces the same one, and its family block is delivered the same way.
EVENT = "cue.fired"
DEFAULT_TIMEOUT = 5.0
KEPT = 200  # outcomes listed at GET /family/, oldest dropped first

# A pull member reads its action from a page in another origin (leo polls from
# the browser), so the routes it reads are readable cross-origin.
OPEN = {"Access-Control-Allow-Origin": "*"}


class Family:
    """The members the hub delivers to, what waits for each pull member, and every outcome."""

    def __init__(
        self,
        members: dict[str, str] | None = None,
        pull: list[str] | tuple[str, ...] = (),
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.members = {name: str(url).rstrip("/") for name, url in (members or {}).items()}
        self.pull = [name for name in pull if name not in self.members]
        self.timeout = float(timeout)
        self._lock = threading.Lock()
        self._waiting: dict[str, dict] = {name: {"cue": None, "action": None} for name in self.pull}
        self._unread: dict[str, tuple[dict, float]] = {}  # pull member -> (outcome, fired)
        self._last: deque[dict] = deque(maxlen=KEPT)
        self._lanes: dict[str, ThreadPoolExecutor] = {}
        self._futures: list[Future] = []

    # --- a cue fires -------------------------------------------------------

    def fire(self, cue: dict, family: Any) -> list[str]:
        """Queue one delivery per member the block names; return their names.

        Returns at once: a push is sent from its member's worker thread.
        """
        if not isinstance(family, dict):
            logger.warning("Cue {} has a family block that is not an object; nothing sent", cue)
            return []
        fired = time.monotonic()
        for name, action in family.items():
            outcome = {"cue": cue, "member": name, "via": None, "status": None, "ms": None,
                       "done": False, "error": None}
            with self._lock:
                self._last.append(outcome)
                if name in self.members:
                    outcome["via"] = "push"
                    future = self._lane(name).submit(
                        self._push, outcome, self.members[name], {"cue": cue, "action": action},
                        fired)
                    self._futures = [f for f in self._futures if not f.done()] + [future]
                elif name in self._waiting:
                    outcome["via"] = "pull"
                    superseded = self._unread.get(name)
                    if superseded is not None:
                        superseded[0].update(
                            done=True,
                            error=f"cue {cue.get('number')} fired before {name} read this one")
                    self._waiting[name] = {"cue": cue, "action": action}
                    self._unread[name] = (outcome, fired)
                else:
                    outcome.update(done=True, error=f"{name} is not a member in [plugins.family]")
                    logger.warning("Cue {} names {}, which [plugins.family] does not", cue, name)
        return list(family)

    def _lane(self, name: str) -> ThreadPoolExecutor:
        if name not in self._lanes:
            self._lanes[name] = ThreadPoolExecutor(max_workers=1,
                                                   thread_name_prefix=f"family-{name}")
        return self._lanes[name]

    def _push(self, outcome: dict, base: str, body: dict, fired: float) -> None:
        status, answer, error = None, None, None
        try:
            response = httpx.post(f"{base}/family/cue", json=body, timeout=self.timeout)
            status = response.status_code
            try:
                answer = response.json()
            except ValueError:
                answer = None
            if not response.is_success:
                said = answer.get("error") if isinstance(answer, dict) else None
                error = str(said) if said else response.text[:200]
        except httpx.TimeoutException as exc:
            error = f"no answer within {self.timeout:g}s ({type(exc).__name__})"
        except Exception as exc:  # down, refused, malformed: recorded, never raised
            error = f"{type(exc).__name__}: {exc}"
        with self._lock:
            outcome.update(status=status, answer=answer, error=error, done=True,
                           ms=round((time.monotonic() - fired) * 1000))
        if error:
            logger.warning("family: {} answered {} for cue {}: {}", outcome["member"], status,
                           body["cue"].get("number"), error)

    # --- what the routes read ----------------------------------------------

    def read(self, name: str) -> dict | None:
        """A pull member's action, or None for a name that is not one; reading delivers it."""
        with self._lock:
            if name not in self._waiting:
                return None
            unread = self._unread.pop(name, None)
            if unread is not None:
                outcome, fired = unread
                outcome.update(status=200, done=True, ms=round((time.monotonic() - fired) * 1000))
            return deepcopy(self._waiting[name])

    def snapshot(self) -> dict:
        with self._lock:
            members = {name: {"via": "push", "url": url} for name, url in self.members.items()}
            members.update({name: {"via": "pull"} for name in self.pull})
            return {"members": members, "last": deepcopy(list(self._last))}

    def wait(self, timeout: float | None = None) -> bool:
        """Block until every queued push has an outcome; True when none is left."""
        with self._lock:
            futures = list(self._futures)
        _, pending = wait_for(futures, timeout=timeout)
        return not pending

    def close(self) -> None:
        for lane in self._lanes.values():
            lane.shutdown(wait=False, cancel_futures=True)
        self._lanes.clear()


def settings_of(config: Any) -> dict:
    """``[plugins.family]`` from a ShowRunnerConfig, or an empty table."""
    if config is None:
        return {}
    return config.plugins.settings.get("family", {}) or {}


def build(settings: dict | None) -> Family:
    """A Family from the ``[plugins.family]`` table; a malformed entry is logged and skipped."""
    settings = settings or {}
    members = settings.get("members") or {}
    if not isinstance(members, dict):
        logger.warning("[plugins.family.members] must be a table of name = url; ignored")
        members = {}
    urls = {}
    for name, url in members.items():
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            urls[name] = url
        else:
            logger.warning("[plugins.family.members] {} = {!r} is not an http URL; ignored",
                           name, url)
    pull = settings.get("pull") or []
    if not isinstance(pull, list) or not all(isinstance(name, str) for name in pull):
        logger.warning("[plugins.family] pull must be a list of names; ignored")
        pull = []
    for name in sorted(set(pull) & set(urls)):
        logger.warning("{} is both pushed to and pulled; it is pushed to", name)
    timeout = settings.get("timeout", DEFAULT_TIMEOUT)
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
        logger.warning("[plugins.family] timeout must be a positive number; {} used",
                       DEFAULT_TIMEOUT)
        timeout = DEFAULT_TIMEOUT
    return Family(urls, pull, timeout)


# The running plugin's Family; the routes read it.
_family: Family | None = None


def configure(settings: dict | None) -> Family:
    """Replace the running Family with one built from ``settings``."""
    global _family
    previous, _family = _family, build(settings)
    if previous is not None:
        previous.close()
    return _family


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/")
async def index(response: Response):
    """The configured members, and the outcome of every recent delivery."""
    response.headers.update(OPEN)
    if _family is None:
        return {"members": {}, "last": []}
    return _family.snapshot()


@router.get("/members/{name}")
async def member(name: str, response: Response):
    """What a pull member is asked to do: the last cue that named it, and its action."""
    response.headers.update(OPEN)
    found = _family.read(name) if _family is not None else None
    if found is None:
        raise HTTPException(status_code=404, headers=OPEN,
                            detail=f"{name} is not a pull member in [plugins.family]")
    return found


# ---------------------------------------------------------------------------
# Plugin class
# ---------------------------------------------------------------------------


class ShowFamilyPlugin:
    """Delivers each fired cue's ``family`` block to the apps of the performing family.

    Configure in show.toml::

        [plugins.family]
        pull = ["leo"]

        [plugins.family.members]
        showstopper = "http://127.0.0.1:18411"
    """

    @showrunner.hookimpl
    def showrunner_register(self):
        return {
            "name": "ShowFamily",
            "description": "Delivers each fired cue's family block to the performing family",
            "version": "0.1.0",
        }

    @showrunner.hookimpl
    def showrunner_startup(self, app):
        family = configure(settings_of(getattr(app, "config", None)))
        logger.info("ShowFamily ready: push to {}, pulled by {}",
                    sorted(family.members) or "none", family.pull or "none")

    @showrunner.hookimpl
    def showrunner_shutdown(self, app):
        global _family
        if _family is not None:
            _family.close()
        _family = None

    @showrunner.hookimpl
    def showrunner_config_changed(self, config, previous_config):
        configure(settings_of(config))

    @showrunner.hookimpl
    def showrunner_event(self, event_name: str, data: dict | None):
        if event_name != EVENT or _family is None or not isinstance(data, dict):
            return
        notes = data.get("notes")
        if not isinstance(notes, dict) or "family" not in notes:
            return
        cue = data.get("cue") or {}
        _family.fire({key: cue.get(key) for key in ("number", "name", "fired_at")},
                     notes["family"])

    @showrunner.hookimpl
    def showrunner_get_routes(self):
        return router

    @showrunner.hookimpl
    def showrunner_get_commands(self):
        return []

    @showrunner.hookimpl
    def showrunner_get_nav(self):
        return None

    @showrunner.hookimpl
    def showrunner_get_status(self):
        return None
