"""Migration paths: fresh DB, legacy (pre-Alembic) DB, upgrade from empty."""

import sqlite3

import pytest
from alembic import command
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from showrunner.database import ShowDatabase


def _rev(db):
    with db.engine.connect() as c:
        return MigrationContext.configure(c).get_current_revision()


def _head(db):
    return ScriptDirectory.from_config(db.alembic_config()).get_current_head()


def test_fresh_db_is_stamped_head(tmp_path):
    db = ShowDatabase(tmp_path / 'a.db')
    db.create_schema()
    assert _rev(db) == _head(db)
    db.create_schema()  # idempotent


def test_legacy_db_requires_upgrade_then_accepts_null_number(tmp_path):
    path = tmp_path / 'legacy.db'
    con = sqlite3.connect(path)
    con.execute(
        'create table cues (id integer primary key, cue_list_id integer not null,'
        ' number integer not null, point integer not null)'
    )
    con.execute('insert into cues values (1, 1, 10, 0)')
    con.commit()
    con.close()

    db = ShowDatabase(path)
    with pytest.raises(RuntimeError, match='sr migration upgrade'):
        db.create_schema()

    command.upgrade(db.alembic_config(), 'head')
    db.create_schema()
    con = sqlite3.connect(path)
    con.execute('insert into cues (cue_list_id, number, point) values (1, NULL, 0)')
    assert {r[0] for r in con.execute('select number from cues')} == {'10', None}


def test_upgrade_on_empty_db_creates_schema(tmp_path):
    db = ShowDatabase(tmp_path / 'empty.db')
    command.upgrade(db.alembic_config(), 'head')
    assert _rev(db) == _head(db)
    db.create_schema()
