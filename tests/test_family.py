"""ShowFamily: each fired cue's family block, delivered to the apps it names.

The members here are fakes on real ports (``tests/conftest.py``), so every
delivery is a real HTTP request. ``SHOWRUNNER_TEST_PORTS=FIRST-LAST`` keeps them
inside a range; unset, the OS picks.
"""

from __future__ import annotations

import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import showrunner.plugins.family as family

CUE = {"number": "3", "name": "Second tune", "fired_at": "2026-10-10T21:00:00.000001+00:00"}


@pytest.fixture(autouse=True)
def no_family_left():
    """Every test starts and ends with no running Family."""
    family._family = None
    yield
    if family._family is not None:
        family._family.close()
    family._family = None


@pytest.fixture()
def client():
    """The family routes on a TestClient."""
    api = FastAPI()
    api.include_router(family.router)
    return TestClient(api)


def outcome(found: dict, member: str) -> dict:
    return next(o for o in found["last"] if o["member"] == member)


# --- push --------------------------------------------------------------------

def test_a_push_member_is_sent_the_cue_and_its_action(fake_member, client):
    clock = fake_member("showstopper")
    hub = family.configure({"members": {"showstopper": clock.url}})

    hub.fire(CUE, {"showstopper": {"clock": "start"}})

    assert hub.wait(10)
    assert clock.received == [{"cue": CUE, "action": {"clock": "start"}}]
    said = outcome(client.get("/family/").json(), "showstopper")
    assert (said["cue"], said["via"], said["status"], said["done"], said["error"]) == \
        (CUE, "push", 200, True, None)
    assert isinstance(said["ms"], int) and said["ms"] >= 0
    assert said["answer"] == {"ok": True, "state": {"seen": 1}}


def test_202_for_work_that_finishes_later_is_a_delivery(fake_member):
    joe = fake_member("joe", understands=("take",), status=202)
    hub = family.configure({"members": {"joe": joe.url}})

    hub.fire(CUE, {"joe": {"take": {"notes": ["C4"], "seconds_per_note": 0.5}}})

    assert hub.wait(10)
    assert outcome(hub.snapshot(), "joe")["status"] == 202


def test_a_member_that_is_down_is_recorded_and_the_others_still_get_theirs(
        fake_member, dead_url):
    """Mutation: the broad except in _push narrowed to TimeoutException -- red here."""
    carlos = fake_member("carlos", understands=("patch",))
    hub = family.configure({"members": {"showstopper": dead_url, "carlos": carlos.url},
                            "timeout": 3})

    hub.fire(CUE, {"showstopper": {"clock": "start"}, "carlos": {"patch": "Moog DFAM"}})

    assert hub.wait(15)
    down = outcome(hub.snapshot(), "showstopper")
    assert (down["status"], down["done"]) == (None, True)
    assert down["error"]
    assert outcome(hub.snapshot(), "carlos")["status"] == 200
    assert carlos.received[0]["action"] == {"patch": "Moog DFAM"}


def test_a_422_is_passed_through_with_the_member_s_own_error(fake_member):
    clock = fake_member("showstopper")
    hub = family.configure({"members": {"showstopper": clock.url}})

    hub.fire(CUE, {"showstopper": {"dance": "now"}})

    assert hub.wait(10)
    said = outcome(hub.snapshot(), "showstopper")
    assert (said["status"], said["error"]) == (422, "no action {'dance': 'now'}")
    assert said["answer"] == {"ok": False, "error": "no action {'dance': 'now'}"}


def test_a_slow_member_times_out_and_firing_never_waits_for_it(fake_member):
    """Mutation: the push run inline in fire() instead of on its lane -- red here."""
    slow = fake_member("showstopper", delay=2.0)
    hub = family.configure({"members": {"showstopper": slow.url}, "timeout": 0.5})

    # Fired from a thread of its own, so a fire() that blocks -- or deadlocks --
    # fails here rather than hanging the suite.
    returned = threading.Event()
    threading.Thread(target=lambda: (hub.fire(CUE, {"showstopper": {"clock": "start"}}),
                                     returned.set()), daemon=True).start()
    assert returned.wait(0.25), "fire() waited for the member"

    assert hub.wait(10)
    said = outcome(hub.snapshot(), "showstopper")
    assert said["status"] is None
    assert said["error"].startswith("no answer within 0.5s")


def test_one_member_s_cues_arrive_in_the_order_they_fired(fake_member):
    """Mutation: eight worker threads per member instead of one -- red here."""
    clock = fake_member("showstopper", delay=0.05)
    hub = family.configure({"members": {"showstopper": clock.url}})

    for number in range(1, 7):
        hub.fire({**CUE, "number": str(number)}, {"showstopper": {"clock": "lap"}})

    assert hub.wait(15)
    assert [body["cue"]["number"] for body in clock.received] == [str(n) for n in range(1, 7)]


# --- pull --------------------------------------------------------------------

def test_a_pull_member_reads_its_action_and_the_read_is_its_delivery(client):
    family.configure({"pull": ["leo"]})
    before = client.get("/family/members/leo")
    assert before.json() == {"cue": None, "action": None}
    assert before.headers["access-control-allow-origin"] == "*"

    family._family.fire(CUE, {"leo": {"song": "Blue Bossa"}})
    waiting = outcome(client.get("/family/").json(), "leo")
    assert (waiting["via"], waiting["status"], waiting["done"]) == ("pull", None, False)

    assert client.get("/family/members/leo").json() == {"cue": CUE,
                                                         "action": {"song": "Blue Bossa"}}
    read = outcome(client.get("/family/").json(), "leo")
    assert (read["status"], read["done"]) == (200, True) and read["ms"] >= 0
    # Read again, the action is the same and the delivery is not counted twice.
    assert client.get("/family/members/leo").json()["action"] == {"song": "Blue Bossa"}
    assert [o["member"] for o in client.get("/family/").json()["last"]] == ["leo"]


def test_a_pull_action_never_read_is_said_to_be_overtaken():
    hub = family.configure({"pull": ["leo"]})
    hub.fire(CUE, {"leo": {"song": "Blue Bossa"}})
    hub.fire({**CUE, "number": "4"}, {"leo": {"song": "Take The A Train"}})

    first, second = hub.snapshot()["last"]
    assert (first["status"], first["done"]) == (None, True)
    assert first["error"] == "cue 4 fired before leo read this one"
    assert hub.read("leo")["action"] == {"song": "Take The A Train"}


def test_only_a_pull_member_has_a_members_route(fake_member, client):
    clock = fake_member("showstopper")
    family.configure({"members": {"showstopper": clock.url}, "pull": ["leo"]})
    for name in ("showstopper", "nobody"):
        missing = client.get(f"/family/members/{name}")
        assert missing.status_code == 404
        assert missing.headers["access-control-allow-origin"] == "*"
    assert client.get("/family/").json()["members"] == {
        "showstopper": {"via": "push", "url": clock.url}, "leo": {"via": "pull"}}


def test_a_name_the_table_does_not_hold_is_recorded_not_sent():
    hub = family.configure({"pull": ["leo"]})
    hub.fire(CUE, {"wolf": {"play": True}})
    said = outcome(hub.snapshot(), "wolf")
    assert (said["via"], said["status"], said["done"]) == (None, None, True)
    assert said["error"] == "wolf is not a member in [plugins.family]"


def test_before_startup_the_routes_answer_empty(client):
    assert client.get("/family/").json() == {"members": {}, "last": []}
    assert client.get("/family/members/leo").status_code == 404


# --- the table -----------------------------------------------------------------

@pytest.mark.parametrize("settings, members, pull, timeout", [
    ({}, {}, [], family.DEFAULT_TIMEOUT),
    ({"members": {"a": "http://h:1/", "b": "ftp://h", "c": 5}}, {"a": "http://h:1"}, [], 5.0),
    ({"members": ["a"], "pull": "leo"}, {}, [], 5.0),
    ({"members": {"leo": "http://h:1"}, "pull": ["leo", "x"]}, {"leo": "http://h:1"}, ["x"], 5.0),
    ({"timeout": 0}, {}, [], family.DEFAULT_TIMEOUT),
    ({"timeout": True}, {}, [], family.DEFAULT_TIMEOUT),
    ({"timeout": 1.5}, {}, [], 1.5),
])
def test_a_malformed_table_entry_is_skipped_not_fatal(settings, members, pull, timeout):
    built = family.build(settings)
    assert (built.members, built.pull, built.timeout) == (members, pull, timeout)


def test_the_plugin_reads_its_table_from_show_toml_and_delivers_on_cue_fired(
        tmp_path, fake_member):
    """The pluggy path end to end: config, startup, the event, the delivery."""
    from showrunner.config import load_config

    clock = fake_member("showstopper")
    config = tmp_path / "show.toml"
    config.write_text(f'[plugins.family]\npull = ["leo"]\n\n[plugins.family.members]\n'
                      f'showstopper = "{clock.url}"\n', encoding="utf-8")

    class App:
        pass

    app = App()
    app.config = load_config(config)
    plugin = family.ShowFamilyPlugin()
    plugin.showrunner_startup(app)
    try:
        plugin.showrunner_event("cue.fired", {"cue": {**CUE, "id": 7}, "show_id": 1,
                                              "notes": {"family": {"showstopper": {"clock": "stop"},
                                                                   "leo": {"song": "X"}}}})
        plugin.showrunner_event("cue.fired", {"cue": CUE, "notes": {"osc": {}}})
        plugin.showrunner_event("show.saved", {"notes": {"family": {"leo": {"song": "Y"}}}})
        assert family._family.wait(10)
        assert clock.received == [{"cue": CUE, "action": {"clock": "stop"}}]
        assert family._family.read("leo") == {"cue": CUE, "action": {"song": "X"}}
    finally:
        plugin.showrunner_shutdown(app)
    assert family._family is None
