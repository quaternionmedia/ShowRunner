"""Tests for the ShowRunner database backend."""

import pytest
from sqlmodel import Session, select

from showrunner.database import ShowDatabase
from showrunner.models import Actor, Config, Cue, CueList, CueLog, Script, Show


@pytest.fixture()
def db(tmp_path):
    """Provide a fresh in-memory-like database for each test."""
    db = ShowDatabase(tmp_path / 'test.db')
    db.create_schema()
    yield db
    db.close()


# ---------------------------------------------------------------------------
# Schema / smoke tests
# ---------------------------------------------------------------------------


def test_create_schema_is_idempotent(db: ShowDatabase):
    """Calling create_schema twice should not raise."""
    db.create_schema()


# ---------------------------------------------------------------------------
# Show CRUD
# ---------------------------------------------------------------------------


def test_create_and_retrieve_show(db: ShowDatabase):
    with db.session() as s:
        show = Show(name='Hamlet', venue='Globe Theatre')
        s.add(show)
        s.commit()
        s.refresh(show)

    retrieved = db.get_show(show.id)
    assert retrieved is not None
    assert retrieved.name == 'Hamlet'
    assert retrieved.venue == 'Globe Theatre'


def test_list_shows_ordered(db: ShowDatabase):
    with db.session() as s:
        s.add(Show(name='Zebra'))
        s.add(Show(name='Alpha'))
        s.commit()

    shows = db.list_shows()
    assert [sh.name for sh in shows] == ['Alpha', 'Zebra']


def test_get_show_returns_none_for_missing(db: ShowDatabase):
    assert db.get_show(999) is None


# ---------------------------------------------------------------------------
# Script
# ---------------------------------------------------------------------------


def test_add_script(db: ShowDatabase):
    with db.session() as s:
        show = Show(name='Test Show')
        s.add(show)
        s.commit()
        s.refresh(show)

        script = Script(
            show_id=show.id, title='Act 1', format='fountain', content='INT. ROOM'
        )
        s.add(script)
        s.commit()
        s.refresh(script)

    with db.session() as s:
        result = s.get(Script, script.id)
        assert result.title == 'Act 1'
        assert result.content == 'INT. ROOM'


# ---------------------------------------------------------------------------
# CueList + Cue
# ---------------------------------------------------------------------------


def test_create_cuelist_with_cues(db: ShowDatabase):
    with db.session() as s:
        show = Show(name='Cue Test')
        s.add(show)
        s.commit()
        s.refresh(show)

        cl = CueList(show_id=show.id, name='Main')
        s.add(cl)
        s.commit()
        s.refresh(cl)

        cues = [
            Cue(
                cue_list_id=cl.id,
                number=1,
                point=0,
                name='House Open',
                layer='Lights',
                sequence=0,
            ),
            Cue(
                cue_list_id=cl.id,
                number=1,
                point=1,
                name='Overture',
                layer='Sound',
                sequence=1,
            ),
            Cue(
                cue_list_id=cl.id,
                number=2,
                point=0,
                name='Blackout',
                layer='Lights',
                sequence=2,
            ),
        ]
        s.add_all(cues)
        s.commit()
        cl_id = cl.id

    with db.session() as s:
        result = s.exec(
            select(Cue).where(Cue.cue_list_id == cl_id).order_by(Cue.number, Cue.point)
        ).all()
        assert len(result) == 3
        assert result[0].name == 'House Open'
        assert result[1].name == 'Overture'
        assert result[2].name == 'Blackout'


def test_cue_script_position(db: ShowDatabase):
    """Cues can store both line number and character offset."""
    with db.session() as s:
        show = Show(name='Position Test')
        s.add(show)
        s.commit()
        s.refresh(show)
        cl = CueList(show_id=show.id)
        s.add(cl)
        s.commit()
        s.refresh(cl)
        cue = Cue(
            cue_list_id=cl.id,
            number=5,
            name='Mid-line',
            layer='Sound',
            script_line=42,
            script_char=15,
        )
        s.add(cue)
        s.commit()
        cue_id = cue.id

    with db.session() as s:
        result = s.get(Cue, cue_id)
        assert result.script_line == 42
        assert result.script_char == 15

    # Update to unpositioned
    with db.session() as s:
        result = s.get(Cue, cue_id)
        result.script_line = None
        result.script_char = None
        s.add(result)
        s.commit()

    with db.session() as s:
        result = s.get(Cue, cue_id)
        assert result.script_line is None
        assert result.script_char is None


# ---------------------------------------------------------------------------
# Actor
# ---------------------------------------------------------------------------


def test_create_actor(db: ShowDatabase):
    with db.session() as s:
        show = Show(name='Actor Test')
        s.add(show)
        s.commit()
        s.refresh(show)

        actor = Actor(show_id=show.id, name='John', channel=3, role='Juror #8')
        s.add(actor)
        s.commit()
        s.refresh(actor)

    with db.session() as s:
        result = s.get(Actor, actor.id)
        assert result.name == 'John'
        assert result.channel == 3
        assert result.active is True


# ---------------------------------------------------------------------------
# CueLog
# ---------------------------------------------------------------------------


def test_create_cue_log(db: ShowDatabase):
    with db.session() as s:
        show = Show(name='Log Test')
        s.add(show)
        s.commit()
        s.refresh(show)

        log = CueLog(show_id=show.id, notes='GO cue 1', duration_ms=150)
        s.add(log)
        s.commit()
        s.refresh(log)

    with db.session() as s:
        result = s.get(CueLog, log.id)
        assert result.notes == 'GO cue 1'
        assert result.duration_ms == 150
        assert result.triggered_at is not None


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_config_key_value(db: ShowDatabase):
    with db.session() as s:
        show = Show(name='Config Test')
        s.add(show)
        s.commit()
        s.refresh(show)

        s.add(Config(show_id=show.id, key='consoleIP', value='192.168.1.1'))
        s.add(Config(show_id=show.id, key='autoConnect', value='1'))
        s.commit()
        show_id = show.id

    with db.session() as s:
        configs = s.exec(
            select(Config).where(Config.show_id == show_id).order_by(Config.key)
        ).all()
        kv = {c.key: c.value for c in configs}
        assert kv == {'autoConnect': '1', 'consoleIP': '192.168.1.1'}


# ---------------------------------------------------------------------------
# Relationships
# ---------------------------------------------------------------------------


def test_cue_log_references_cue(db: ShowDatabase):
    with db.session() as s:
        show = Show(name='Rel Test')
        s.add(show)
        s.commit()
        s.refresh(show)

        cl = CueList(show_id=show.id, name='Main')
        s.add(cl)
        s.commit()
        s.refresh(cl)

        cue = Cue(cue_list_id=cl.id, number=5, point=0, name='Spot On')
        s.add(cue)
        s.commit()
        s.refresh(cue)

        log = CueLog(show_id=show.id, cue_id=cue.id, notes='fired')
        s.add(log)
        s.commit()
        s.refresh(log)
        log_id = log.id
        cue_id = cue.id

    with db.session() as s:
        result = s.get(CueLog, log_id)
        assert result.cue_id == cue_id
