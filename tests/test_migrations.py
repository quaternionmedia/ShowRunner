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


def test_cue_groups_migration_keeps_cues_and_downgrades(tmp_path):
    path = tmp_path / 'groups.db'
    con = sqlite3.connect(path)  # the schema as it was before cue groups
    con.executescript(
        'create table shows (id integer primary key, name varchar not null, venue varchar,'
        ' created_at datetime not null, updated_at datetime not null);'
        'create table cue_lists (id integer primary key, show_id integer not null,'
        ' name varchar not null, description varchar, created_at datetime not null);'
        'create table cues (id integer primary key, cue_list_id integer not null, number varchar,'
        ' point integer not null default 0, name varchar, layer varchar, cue_type varchar,'
        ' notes varchar, color varchar, sequence integer not null default 0, script_line integer,'
        ' script_char integer, created_at datetime not null);'
        "insert into shows values (1, 'Hamlet', null, '2026-01-01', '2026-01-01');"
        "insert into cue_lists values (1, 1, 'Main', null, '2026-01-01');"
        "insert into cues (id, cue_list_id, number, name, layer, created_at)"
        " values (1, 1, '5', 'a', 'Lights', '2026-01-01');"
    )
    con.commit()
    con.close()

    db = ShowDatabase(path)
    cfg = db.alembic_config()
    command.stamp(cfg, 'b3f1d0a7c921')
    command.upgrade(cfg, 'head')
    assert _rev(db) == _head(db)
    con = sqlite3.connect(path)
    assert 'group_id' in {r[1] for r in con.execute('pragma table_info(cues)')}
    assert con.execute('select name, group_id from cues where id = 1').fetchall() == [('a', None)]
    con.close()

    command.downgrade(cfg, '-1')  # the cue QLab-link columns go first
    con = sqlite3.connect(path)
    assert not {'external_id', 'external_number'} & {r[1] for r in con.execute('pragma table_info(cues)')}
    con.close()

    command.upgrade(cfg, 'head')
    command.downgrade(cfg, '-2')  # then the group number columns
    con = sqlite3.connect(path)
    assert not {'layer', 'number'} & {r[1] for r in con.execute('pragma table_info(cue_groups)')}
    con.close()

    command.downgrade(cfg, '-1')  # then the groups migration itself
    con = sqlite3.connect(path)
    assert 'group_id' not in {r[1] for r in con.execute('pragma table_info(cues)')}
    assert con.execute("select name from sqlite_master where name = 'cue_groups'").fetchall() == []
    assert con.execute('select name from cues').fetchall() == [('a',)]
    con.close()
