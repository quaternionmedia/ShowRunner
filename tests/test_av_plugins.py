"""Tests for the AV plugins: ShowVoicer, ShowRecorder, ShowProgrammer.

These tests cover pure logic functions (no AV hardware required) plus route
behaviour via FastAPI TestClient.  Optional dependencies (kokoro, simpleobsws,
python-osc) are not installed in the dev environment, so tests targeting those
code paths validate graceful-degradation rather than real hardware I/O.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from showrunner.database import ShowDatabase
from showrunner.models import Cue, CueList, CueLog, Show

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _MockApp:
    """Minimal stand-in for a ShowRunner app instance used in route tests."""

    def __init__(self, db: ShowDatabase):
        self.db = db
        self.config = None  # _get_cfg() falls back to {} when config is None


def _make_programmer_client(db: ShowDatabase):
    import showrunner.plugins.programmer as prog

    prog._app_ref = _MockApp(db)
    prog._cue_pointer = 0
    prog._active_cues = []
    prog._active_cue_list_id = None

    app = FastAPI()
    app.include_router(prog.router)
    return TestClient(app)


def _make_recorder_client(db: ShowDatabase | None = None):
    import showrunner.plugins.recorder as rec

    rec._app_ref = _MockApp(db) if db else None

    app = FastAPI()
    app.include_router(rec.router)
    return TestClient(app)


# ---------------------------------------------------------------------------
# ShowVoicer — parse_narrator_blocks (pure function, no deps)
# ---------------------------------------------------------------------------


def test_parse_empty_script_returns_empty_list():
    from showrunner.plugins.voicer import parse_narrator_blocks

    assert parse_narrator_blocks("") == []


def test_parse_single_narrator_block():
    from showrunner.plugins.voicer import parse_narrator_blocks

    script = "NARRATOR (V.O.)\nHello world.\n"
    blocks = parse_narrator_blocks(script)
    assert len(blocks) == 1
    assert blocks[0]["text"] == "Hello world."
    assert blocks[0]["index"] == 1
    assert blocks[0]["page"] == "intro"  # default page before any [[Page ...]]


def test_parse_narrator_block_tracks_page_label():
    from showrunner.plugins.voicer import parse_narrator_blocks

    script = "[[Page paradox]]\n\nNARRATOR (V.O.)\nFirst line.\n"
    blocks = parse_narrator_blocks(script)
    assert blocks[0]["page"] == "paradox"


def test_parse_multiple_blocks_assigned_sequential_indices():
    from showrunner.plugins.voicer import parse_narrator_blocks

    script = (
        "[[Page system]]\n\n"
        "NARRATOR (V.O.)\nFirst.\n\n"
        "[[Page meta]]\n\n"
        "NARRATOR (V.O.)\nSecond.\n"
    )
    blocks = parse_narrator_blocks(script)
    assert len(blocks) == 2
    assert blocks[0]["index"] == 1
    assert blocks[0]["page"] == "system"
    assert blocks[1]["index"] == 2
    assert blocks[1]["page"] == "meta"


def test_parse_joins_multiline_dialogue():
    from showrunner.plugins.voicer import parse_narrator_blocks

    script = "NARRATOR (V.O.)\nLine one.\nLine two.\nLine three.\n"
    blocks = parse_narrator_blocks(script)
    assert blocks[0]["text"] == "Line one. Line two. Line three."


def test_parse_skips_parentheticals():
    from showrunner.plugins.voicer import parse_narrator_blocks

    script = "NARRATOR (V.O.)\n(beat)\nActual dialogue.\n"
    blocks = parse_narrator_blocks(script)
    assert len(blocks) == 1
    assert blocks[0]["text"] == "Actual dialogue."


def test_parse_narrator_case_insensitive():
    from showrunner.plugins.voicer import parse_narrator_blocks

    script = "narrator (v.o.)\nLower case works.\n"
    blocks = parse_narrator_blocks(script)
    assert len(blocks) == 1


def test_parse_intro_script_produces_six_blocks():
    """The bundled fountain script has exactly 6 NARRATOR (V.O.) blocks."""
    from showrunner.plugins.voicer import parse_narrator_blocks

    script_path = Path(__file__).parent.parent / "examples/scripts/showrunner-intro.fountain"
    if not script_path.exists():
        pytest.skip("Fountain script not present")
    blocks = parse_narrator_blocks(script_path.read_text(encoding="utf-8"))
    assert len(blocks) == 6


# ---------------------------------------------------------------------------
# ShowVoicer — _ardour_session_xml (pure function, no deps)
# ---------------------------------------------------------------------------


def test_ardour_xml_empty_list():
    from showrunner.plugins.voicer import _ardour_session_xml

    xml = _ardour_session_xml([])
    assert "<Session" in xml
    assert "<Sources>" in xml


def test_ardour_xml_offsets_use_48khz(tmp_path):
    """Offsets must be calculated at 48 kHz, not 24 kHz."""
    from showrunner.plugins.voicer import _ARDOUR_SAMPLE_RATE, _ardour_session_xml

    paths = [tmp_path / "vo-1.wav", tmp_path / "vo-2.wav"]
    xml = _ardour_session_xml(paths, total_duration_ms=2000)

    # First clip is at position 0; second should be at 1000 ms * 48 kHz = 48000 samples
    expected_offset = int(0.5 * 2000 / 1000 * _ARDOUR_SAMPLE_RATE)  # 48000
    assert f'position="{expected_offset}"' in xml


def test_ardour_xml_length_attribute_present(tmp_path):
    """Every Region must have a length attribute (even if 0 for unreadable files)."""
    from showrunner.plugins.voicer import _ardour_session_xml

    paths = [tmp_path / "nonexistent.wav"]
    xml = _ardour_session_xml(paths)
    assert 'length="0"' in xml  # unreadable → 0, but attribute must be present


def test_wav_length_returns_zero_for_missing_file(tmp_path):
    from showrunner.plugins.voicer import _wav_length_ardour_samples

    assert _wav_length_ardour_samples(tmp_path / "ghost.wav") == 0


def test_ardour_xml_has_one_region_per_wav(tmp_path):
    from showrunner.plugins.voicer import _ardour_session_xml

    paths = [tmp_path / f"vo-{i}.wav" for i in range(3)]
    xml = _ardour_session_xml(paths)
    assert xml.count("<Region ") == 3
    assert xml.count("<Source ") == 3


# ---------------------------------------------------------------------------
# ShowVoicer — generate_wav graceful degradation
# ---------------------------------------------------------------------------


def test_generate_wav_returns_false_when_kokoro_missing(tmp_path):
    """Without kokoro installed, generate_wav returns False and doesn't raise."""
    import sys

    # Ensure kokoro is absent (it won't be in dev env, but guard explicitly)
    if "kokoro" in sys.modules:
        pytest.skip("kokoro is installed — skipping absence test")

    from showrunner.plugins.voicer import generate_wav

    result = generate_wav("Hello.", "af_heart", 1.0, tmp_path / "out.wav")
    assert result is False
    assert not (tmp_path / "out.wav").exists()


def test_pipeline_is_cached(monkeypatch):
    """_get_pipeline() only constructs KPipeline once across multiple calls."""
    import showrunner.plugins.voicer as voicer_mod

    call_count = {"n": 0}

    class FakePipeline:
        pass

    class FakeKPipeline:
        def __init__(self, *a, **kw):
            call_count["n"] += 1

        def __call__(self, *a, **kw):
            return iter([])

    original_pipeline = voicer_mod._pipeline
    monkeypatch.setattr(voicer_mod, "_pipeline", None)

    with patch.dict("sys.modules", {"kokoro": type("m", (), {"KPipeline": FakeKPipeline})()}):
        voicer_mod._get_pipeline()
        voicer_mod._get_pipeline()

    assert call_count["n"] == 1
    voicer_mod._pipeline = original_pipeline  # restore


# ---------------------------------------------------------------------------
# ShowRecorder — _build_mlt_xml (pure function, no deps)
# ---------------------------------------------------------------------------


def test_build_mlt_xml_empty_logs():
    from showrunner.plugins.recorder import _build_mlt_xml

    xml = _build_mlt_xml([], None)
    assert "main_bin" in xml
    assert "<entry" not in xml


def test_build_mlt_xml_creates_one_entry_per_log():
    from showrunner.plugins.recorder import _build_mlt_xml

    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    logs = [
        CueLog(
            show_id=1,
            triggered_at=now,
            duration_ms=2000,
            notes=json.dumps({"scene": "Terminal"}),
        ),
        CueLog(
            show_id=1,
            triggered_at=now.replace(second=5),
            duration_ms=3000,
            notes=json.dumps({"scene": "Dashboard"}),
        ),
    ]
    xml = _build_mlt_xml(logs, None)
    assert xml.count("<entry") == 2
    assert 'comment="Terminal"' in xml
    assert 'comment="Dashboard"' in xml


def test_build_mlt_xml_offset_derived_from_triggered_at():
    from showrunner.plugins.recorder import _build_mlt_xml

    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 1, 1, 12, 0, 2, tzinfo=timezone.utc)  # 2000 ms later
    logs = [
        CueLog(show_id=1, triggered_at=t0, duration_ms=1000),
        CueLog(show_id=1, triggered_at=t1, duration_ms=1000),
    ]
    xml = _build_mlt_xml(logs, None)
    assert 'in="0ms"' in xml
    assert 'in="2000ms"' in xml


# ---------------------------------------------------------------------------
# ShowRecorder — /status route
# ---------------------------------------------------------------------------


def test_recorder_status_when_not_started():
    """Status returns 'not_started' before plugin is initialised."""
    import showrunner.plugins.recorder as rec

    rec._app_ref = None
    client = _make_recorder_client(None)
    # _make_recorder_client already sets _app_ref=None for db=None
    response = client.get("/recorder/status")
    assert response.status_code == 200
    assert response.json()["obs"] == "not_started"
    assert response.json()["recording"] is False


def test_recorder_status_not_connected_when_obs_unreachable(tmp_path):
    db = ShowDatabase(tmp_path / "rec.db")
    db.create_schema()
    client = _make_recorder_client(db)

    with patch(
        "showrunner.plugins.recorder._obs_request", new=AsyncMock(return_value=None)
    ):
        response = client.get("/recorder/status")

    assert response.json() == {"obs": "not_connected", "recording": False}
    db.close()


def test_recorder_status_connected_when_obs_responds(tmp_path):
    db = ShowDatabase(tmp_path / "rec.db")
    db.create_schema()
    client = _make_recorder_client(db)

    with patch(
        "showrunner.plugins.recorder._obs_request",
        new=AsyncMock(return_value={"outputActive": True}),
    ):
        response = client.get("/recorder/status")

    assert response.json() == {"obs": "connected", "recording": True}
    db.close()


# ---------------------------------------------------------------------------
# ShowRecorder — /record route (stop-failure fix)
# ---------------------------------------------------------------------------


def test_recorder_stop_returns_warning_when_obs_unreachable(tmp_path):
    """When OBS is unreachable, /record returns 200 with ok=False and a warning."""
    db = ShowDatabase(tmp_path / "rec.db")
    db.create_schema()
    client = _make_recorder_client(db)

    with patch(
        "showrunner.plugins.recorder._obs_request", new=AsyncMock(return_value=None)
    ):
        response = client.post("/recorder/record?action=stop")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "warning" in body
    db.close()


def test_recorder_start_returns_warning_when_obs_unreachable(tmp_path):
    """When OBS is unreachable, /record returns 200 with ok=False and a warning."""
    db = ShowDatabase(tmp_path / "rec.db")
    db.create_schema()
    client = _make_recorder_client(db)

    with patch(
        "showrunner.plugins.recorder._obs_request", new=AsyncMock(return_value=None)
    ):
        response = client.post("/recorder/record?action=start")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "warning" in body
    db.close()


def test_recorder_record_ok_when_obs_responds(tmp_path):
    db = ShowDatabase(tmp_path / "rec.db")
    db.create_schema()
    client = _make_recorder_client(db)

    with patch(
        "showrunner.plugins.recorder._obs_request",
        new=AsyncMock(return_value={"outputPath": "/tmp/recording.mkv"}),
    ):
        response = client.post("/recorder/record?action=stop")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    db.close()


# ---------------------------------------------------------------------------
# ShowProgrammer — _broadcast_osc graceful degradation
# ---------------------------------------------------------------------------


def test_broadcast_osc_returns_zero_when_python_osc_missing():
    """Without python-osc installed, broadcast returns 0 and doesn't raise."""
    import sys

    if "pythonosc" in sys.modules:
        pytest.skip("python-osc is installed — skipping absence test")

    from showrunner.plugins.programmer import _broadcast_osc

    sent = _broadcast_osc(
        [{"host": "localhost", "port": 9000}], "/test/address", []
    )
    assert sent == 0


# ---------------------------------------------------------------------------
# ShowProgrammer — /go routes
# ---------------------------------------------------------------------------


def _seed_cue_list(db: ShowDatabase, show_id: int, name: str, cue_count: int) -> int:
    """Insert a cue list with ``cue_count`` cues, return list id."""
    with db.session() as session:
        cue_list = CueList(show_id=show_id, name=name)
        session.add(cue_list)
        session.flush()
        for n in range(1, cue_count + 1):
            session.add(
                Cue(
                    cue_list_id=cue_list.id,
                    number=n,
                    point=0,
                    name=f"{name}-cue-{n}",
                    notes=None,
                )
            )
        session.commit()
        return cue_list.id


@pytest.fixture()
def programmer_env(tmp_path):
    """Yield (client, db, show_id, list_a_id, list_b_id) with clean state."""
    import showrunner.plugins.programmer as prog

    db = ShowDatabase(tmp_path / "prog.db")
    db.create_schema()

    with db.session() as session:
        show = Show(name="Test Show")
        session.add(show)
        session.commit()
        show_id = show.id

    list_a = _seed_cue_list(db, show_id, "A", 3)
    list_b = _seed_cue_list(db, show_id, "B", 2)

    client = _make_programmer_client(db)

    yield client, db, show_id, list_a, list_b

    prog._app_ref = None
    db.close()


def test_programmer_go_fires_first_cue(programmer_env):
    client, db, show_id, list_a, _ = programmer_env
    response = client.post(f"/programmer/go?cue_list_id={list_a}&show_id={show_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["number"] == 1
    assert data["pointer"] == 1


def test_programmer_go_advances_pointer_sequentially(programmer_env):
    client, db, show_id, list_a, _ = programmer_env
    for expected in range(1, 4):
        response = client.post(f"/programmer/go?cue_list_id={list_a}&show_id={show_id}")
        assert response.status_code == 200
        assert response.json()["number"] == expected


def test_programmer_go_past_end_returns_409(programmer_env):
    client, db, show_id, list_a, _ = programmer_env
    for _ in range(3):
        client.post(f"/programmer/go?cue_list_id={list_a}&show_id={show_id}")
    response = client.post(f"/programmer/go?cue_list_id={list_a}&show_id={show_id}")
    assert response.status_code == 409


def test_programmer_go_resets_pointer_on_list_change(programmer_env):
    """Switching cue_list_id must restart the pointer from the first cue."""
    client, db, show_id, list_a, list_b = programmer_env

    # Advance two steps in list A
    client.post(f"/programmer/go?cue_list_id={list_a}&show_id={show_id}")
    client.post(f"/programmer/go?cue_list_id={list_a}&show_id={show_id}")

    # Switch to list B — must start from cue 1, not continue from pointer=2
    response = client.post(f"/programmer/go?cue_list_id={list_b}&show_id={show_id}")
    assert response.status_code == 200
    assert response.json()["number"] == 1


def test_programmer_reset_zeroes_pointer(programmer_env):
    client, db, show_id, list_a, _ = programmer_env

    client.post(f"/programmer/go?cue_list_id={list_a}&show_id={show_id}")
    client.post(f"/programmer/go?cue_list_id={list_a}&show_id={show_id}")

    reset_response = client.post("/programmer/reset")
    assert reset_response.json()["pointer"] == 0

    # After reset, next GO should fire cue 1 again
    go_response = client.post(f"/programmer/go?cue_list_id={list_a}&show_id={show_id}")
    assert go_response.json()["number"] == 1


def test_programmer_status_reflects_pointer(programmer_env):
    client, db, show_id, list_a, _ = programmer_env

    client.post(f"/programmer/go?cue_list_id={list_a}&show_id={show_id}")
    status = client.get("/programmer/status")
    assert status.json()["pointer"] == 1


def test_programmer_fire_by_number(programmer_env):
    client, db, show_id, list_a, _ = programmer_env
    response = client.post(f"/programmer/cue/2?cue_list_id={list_a}&show_id={show_id}")
    assert response.status_code == 200
    assert response.json()["number"] == 2


def test_programmer_fire_by_invalid_number_returns_400(programmer_env):
    client, db, show_id, list_a, _ = programmer_env
    response = client.post(f"/programmer/cue/abc?cue_list_id={list_a}&show_id={show_id}")
    assert response.status_code == 400


def test_programmer_go_writes_cuelog_entry(programmer_env):
    """Firing a cue must create a CueLog record in the database."""
    from sqlmodel import select

    client, db, show_id, list_a, _ = programmer_env
    client.post(f"/programmer/go?cue_list_id={list_a}&show_id={show_id}")

    with db.session() as session:
        logs = session.exec(select(CueLog).where(CueLog.show_id == show_id)).all()
    assert len(logs) == 1
    assert logs[0].triggered_at is not None


# ---------------------------------------------------------------------------
# ShowProgrammer — HTTP cue dispatch (_fire_cue with "http" key in notes)
# ---------------------------------------------------------------------------


def test_fire_cue_dispatches_http_when_notes_has_http_key(programmer_env):
    """A cue with an 'http' notes key must call _dispatch_http."""

    client, db, show_id, list_a, _ = programmer_env

    # Attach an http payload to cue 1 in list A
    with db.session() as session:
        from sqlmodel import select as _select
        cue = session.exec(
            _select(Cue).where(Cue.cue_list_id == list_a).where(Cue.number == 1)
        ).first()
        cue.notes = json.dumps(
            {"http": {"method": "POST", "path": "/recorder/scene", "params": {"scene": "Terminal"}}}
        )
        session.add(cue)
        session.commit()

    with patch("showrunner.plugins.programmer._dispatch_http", return_value=True) as mock_http:
        response = client.post(f"/programmer/go?cue_list_id={list_a}&show_id={show_id}")

    assert response.status_code == 200
    assert response.json().get("http_ok") is True
    mock_http.assert_called_once()
    args = mock_http.call_args
    assert args[1]["path"] == "/recorder/scene"
    assert args[1]["params"] == {"scene": "Terminal"}


def test_fire_cue_http_failure_is_reflected_in_response(programmer_env):
    """When _dispatch_http returns False, http_ok in the response must be False."""

    client, db, show_id, list_a, _ = programmer_env

    with db.session() as session:
        from sqlmodel import select as _select
        cue = session.exec(
            _select(Cue).where(Cue.cue_list_id == list_a).where(Cue.number == 1)
        ).first()
        cue.notes = json.dumps({"http": {"method": "POST", "path": "/fail"}})
        session.add(cue)
        session.commit()

    with patch("showrunner.plugins.programmer._dispatch_http", return_value=False):
        response = client.post(f"/programmer/go?cue_list_id={list_a}&show_id={show_id}")

    assert response.status_code == 200
    assert response.json().get("http_ok") is False


# ---------------------------------------------------------------------------
# ShowRecorder — _build_mlt_xml with obs_output_dir
# ---------------------------------------------------------------------------


def test_build_mlt_xml_uses_obs_recordings_when_dir_set(tmp_path):
    """When obs_output_dir contains recordings, producers must have resource= attributes."""
    from showrunner.plugins.recorder import _build_mlt_xml

    # Create a fake recording file
    recording = tmp_path / "2026-01-01_scene1.mkv"
    recording.write_bytes(b"")

    now = datetime(2026, 1, 1, 12, 0, 0)
    logs = [
        CueLog(show_id=1, triggered_at=now, duration_ms=3000, notes='{"scene": "Terminal"}'),
    ]
    xml = _build_mlt_xml(logs, None, obs_output_dir=str(tmp_path))

    assert f'resource="{recording.as_posix()}"' in xml
    assert "<!-- TODO" not in xml


def test_build_mlt_xml_stubs_producers_when_no_recordings(tmp_path):
    """Without recordings, producers must include a TODO comment."""
    from showrunner.plugins.recorder import _build_mlt_xml

    # Empty directory — no video files
    now = datetime(2026, 1, 1, 12, 0, 0)
    logs = [
        CueLog(show_id=1, triggered_at=now, duration_ms=2000, notes='{"scene": "Dashboard"}'),
    ]
    xml = _build_mlt_xml(logs, None, obs_output_dir=str(tmp_path))

    assert "<!-- TODO" in xml


def test_build_mlt_xml_handles_aware_and_naive_datetimes():
    """_build_mlt_xml must not crash when triggered_at datetimes are timezone-aware."""
    from showrunner.plugins.recorder import _build_mlt_xml

    aware = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    naive = datetime(2026, 1, 1, 12, 0, 5)  # 5 s later, naive

    logs = [
        CueLog(show_id=1, triggered_at=aware, duration_ms=1000),
        CueLog(show_id=1, triggered_at=naive, duration_ms=1000),
    ]
    # Must not raise TypeError: can't subtract offset-naive and offset-aware datetimes
    xml = _build_mlt_xml(logs, None)
    assert xml.count("<entry") == 2


# ---------------------------------------------------------------------------
# ShowVoicer — /voicer/generate 503 when kokoro absent
# ---------------------------------------------------------------------------


def _make_voicer_client(db: ShowDatabase):
    import showrunner.plugins.voicer as voicer

    voicer._app_ref = _MockApp(db)

    app = FastAPI()
    app.include_router(voicer.router)
    return TestClient(app)


def test_voicer_generate_503_when_kokoro_not_installed(tmp_path):
    """POST /voicer/generate must return 503 if kokoro is absent and no audio was generated."""
    import sys

    from showrunner.models import Script

    if "kokoro" in sys.modules:
        pytest.skip("kokoro is installed — skipping absence test")

    db = ShowDatabase(tmp_path / "v.db")
    db.create_schema()

    with db.session() as session:
        from showrunner.models import Show as ShowModel
        show = ShowModel(name="VO Test")
        session.add(show)
        session.flush()
        script = Script(
            show_id=show.id,
            title="VO Script",
            format="fountain",
            content="NARRATOR (V.O.)\nTest line.\n",
        )
        session.add(script)
        session.commit()
        script_id = script.id
        show_id = show.id

    client = _make_voicer_client(db)
    response = client.post(f"/voicer/generate?show_id={show_id}&script_id={script_id}")
    assert response.status_code == 503
    assert "kokoro" in response.json()["detail"].lower()

    db.close()


# ---------------------------------------------------------------------------
# ShowProgrammer — module-level timing globals
# ---------------------------------------------------------------------------


def test_go_sets_timing_globals(programmer_env):
    """Firing a cue via GO must set _last_fire_at, _last_fire_name, _show_start_at."""
    import showrunner.plugins.programmer as prog
    from datetime import datetime

    client, db, show_id, list_a, _ = programmer_env
    # Ensure clean state
    client.post("/programmer/reset")

    assert prog._last_fire_at is None
    assert prog._show_start_at is None

    before = datetime.now()
    client.post(f"/programmer/go?cue_list_id={list_a}&show_id={show_id}")
    after = datetime.now()

    assert prog._last_fire_at is not None
    assert before <= prog._last_fire_at <= after, (
        "_last_fire_at must be a naive local datetime close to now()"
    )
    # tzinfo must be None — a tz-aware datetime would cause a negative elapsed
    # offset when subtracted from datetime.now() in a non-UTC timezone.
    assert prog._last_fire_at.tzinfo is None
    assert prog._last_fire_name.startswith("1")   # cue number 1
    assert prog._show_start_at == prog._last_fire_at


def test_show_start_not_overwritten_on_second_fire(programmer_env):
    """_show_start_at is set only on the first GO; subsequent fires leave it alone."""
    import showrunner.plugins.programmer as prog

    client, db, show_id, list_a, _ = programmer_env
    client.post("/programmer/reset")

    client.post(f"/programmer/go?cue_list_id={list_a}&show_id={show_id}")
    first_start = prog._show_start_at

    client.post(f"/programmer/go?cue_list_id={list_a}&show_id={show_id}")
    assert prog._show_start_at is first_start   # same object, not replaced


def test_reset_clears_timing_globals(programmer_env):
    """POST /programmer/reset must zero _last_fire_at, _last_fire_name, _show_start_at."""
    import showrunner.plugins.programmer as prog

    client, db, show_id, list_a, _ = programmer_env
    client.post(f"/programmer/go?cue_list_id={list_a}&show_id={show_id}")
    assert prog._last_fire_at is not None

    client.post("/programmer/reset")

    assert prog._last_fire_at is None
    assert prog._last_fire_name == ""
    assert prog._show_start_at is None


# ---------------------------------------------------------------------------
# ShowProgrammer — timing helpers (_fmt, _fmt_ms, _cue_durations_from_logs)
# ---------------------------------------------------------------------------


from showrunner.plugins.programmer import (
    _cue_durations_from_logs,
    _fmt,
    _fmt_ms,
)


class _FakeLog:
    """Minimal CueLog stand-in for unit-testing _cue_durations_from_logs."""

    def __init__(self, cue_id, triggered_at, duration_ms=None):
        from datetime import datetime, timezone
        self.cue_id = cue_id
        self.triggered_at = triggered_at
        self.duration_ms = duration_ms


# --- _fmt -------------------------------------------------------------------


def test_fmt_zero():
    assert _fmt(0) == "00:00"


def test_fmt_under_one_hour():
    assert _fmt(65) == "01:05"
    assert _fmt(59) == "00:59"
    assert _fmt(3599) == "59:59"


def test_fmt_exactly_one_hour():
    assert _fmt(3600) == "01:00:00"


def test_fmt_over_one_hour():
    assert _fmt(3661) == "01:01:01"
    assert _fmt(7322) == "02:02:02"


def test_fmt_fractional_seconds_truncated():
    # Sub-second component should be dropped, not rounded
    assert _fmt(1.9) == "00:01"
    assert _fmt(3599.99) == "59:59"


# --- _fmt_ms ----------------------------------------------------------------


def test_fmt_ms_zero():
    assert _fmt_ms(0) == "00:00.00"


def test_fmt_ms_half_second():
    assert _fmt_ms(0.5) == "00:00.50"


def test_fmt_ms_whole_seconds():
    assert _fmt_ms(65.0) == "01:05.00"


def test_fmt_ms_centiseconds():
    assert _fmt_ms(1.5) == "00:01.50"
    assert _fmt_ms(65.25) == "01:05.25"


def test_fmt_ms_over_one_hour():
    assert _fmt_ms(3661.99) == "01:01:01.99"


def test_fmt_ms_centiseconds_capped_at_99():
    # Floating-point near-integers like 1.999… should not overflow to .100
    result = _fmt_ms(1.9999)
    assert result.endswith(".99") or result.endswith(".00"), result


# --- _cue_durations_from_logs -----------------------------------------------


def _make_logs(*entries):
    """Build fake logs: each entry is (cue_id, seconds_offset, duration_ms=None)."""
    from datetime import datetime, timedelta
    base = datetime(2025, 1, 1, 12, 0, 0)
    logs = []
    for item in entries:
        if len(item) == 2:
            cue_id, offset = item
            duration_ms = None
        else:
            cue_id, offset, duration_ms = item
        logs.append(_FakeLog(cue_id, base + timedelta(seconds=offset), duration_ms))
    return logs


def test_cue_durations_empty():
    assert _cue_durations_from_logs([]) == {}


def test_cue_durations_single_entry():
    # One entry: no next log, so duration unknown
    logs = _make_logs((1, 0))
    assert _cue_durations_from_logs(logs) == {}


def test_cue_durations_two_entries_inferred():
    logs = _make_logs((1, 0), (2, 5))
    result = _cue_durations_from_logs(logs)
    assert result[1] == 5000   # 5 s gap → 5000 ms
    assert 2 not in result     # last entry has no duration yet


def test_cue_durations_explicit_duration_ms_wins():
    logs = _make_logs((1, 0, 1234), (2, 10))
    result = _cue_durations_from_logs(logs)
    assert result[1] == 1234   # explicit value used, not the 10 s gap


def test_cue_durations_most_recent_firing_wins():
    # Cue 1 fires twice; only the second duration should be returned
    logs = _make_logs((1, 0), (2, 3), (1, 10), (2, 15))
    result = _cue_durations_from_logs(logs)
    assert result[1] == 5000   # second firing: 15 - 10 = 5 s
    assert result[2] == 7000   # 10 - 3 = 7 s


def test_cue_durations_skips_none_cue_id():
    logs = _make_logs((None, 0), (1, 5))
    result = _cue_durations_from_logs(logs)
    assert None not in result
    assert 1 not in result   # no entry after cue_id=1


def test_cue_durations_skips_negative_delta():
    # Timestamps out of order or equal → no negative durations stored
    from datetime import datetime
    base = datetime(2025, 1, 1, 12, 0, 0)
    logs = [
        _FakeLog(1, base),
        _FakeLog(2, base),  # same time → delta = 0
    ]
    result = _cue_durations_from_logs(logs)
    assert 1 not in result
