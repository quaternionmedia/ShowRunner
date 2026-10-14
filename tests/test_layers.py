"""Layers table: migration seeding, model constraints, and the layer registry."""

import sqlite3

import pluggy
import pytest
from alembic import command
from sqlmodel import select

import showrunner
from showrunner import layers as L
from showrunner.database import ShowDatabase
from showrunner.hookspecs import ShowRunnerSpec
from showrunner.models import Cue, CueList, Layer, Show


def _legacy_db(path):
    """A pre-layers database: one show, one list, cues using a non-default layer name."""
    con = sqlite3.connect(path)
    con.executescript("""
        create table shows (id integer primary key, name text not null);
        create table cue_lists (id integer primary key, show_id integer not null);
        create table cues (id integer primary key, cue_list_id integer not null,
                           number integer not null, point integer not null, layer text);
        create table alembic_version (version_num varchar(32) not null);
        insert into alembic_version values ('2a50c1c7c24c');
        insert into shows values (1, 'Hamlet');
        insert into cue_lists values (1, 1);
        insert into cues values (1, 1, 10, 0, 'Lights'), (2, 1, 11, 0, 'Mix'), (3, 1, 12, 0, NULL);
        """)
    con.commit()
    con.close()


def test_upgrade_seeds_defaults_and_existing_cue_layers(tmp_path):
    path = tmp_path / "legacy.db"
    _legacy_db(path)
    db = ShowDatabase(path)
    command.upgrade(db.alembic_config(), "head")

    con = sqlite3.connect(path)
    rows = con.execute(
        "select name, color, type, sequence from layers where show_id = 1 order by sequence"
    ).fetchall()
    assert rows == [
        ("Lights", "orange", "lights", 0),
        ("Sound", "blue", "sound", 1),
        ("Video", "purple", "video", 2),
        ("Audio", "cyan", "audio", 3),
        ("Stage", "green", "stage", 4),
        ("Mix", "grey", "generic", 5),
    ]
    # cues are untouched
    assert con.execute("select count(*) from cues").fetchone()[0] == 3


def test_fresh_db_has_no_seeded_layers_until_show_exists(tmp_path):
    db = ShowDatabase(tmp_path / "fresh.db")
    db.create_schema()
    with db.engine.connect() as c:
        assert c.exec_driver_sql("select count(*) from layers").scalar() == 0


def _pm(*plugins):
    pm = pluggy.PluginManager("showrunner")
    pm.add_hookspecs(ShowRunnerSpec)
    for p in plugins:
        pm.register(p)
    return pm


class TheatreMix:
    """Canonical plugin extension: registers the `mix` type and a Mix layer."""

    @showrunner.hookimpl
    def showrunner_get_layer_types(self):
        return {
            "type": "mix",
            "label": "Mix",
            "description": "TheatreMix cues",
            "color": "pink",
        }

    @showrunner.hookimpl
    def showrunner_get_layers(self):
        return {"name": "Mix", "color": "pink", "type": "mix"}


class Broken:
    @showrunner.hookimpl
    def showrunner_get_layer_types(self):
        raise RuntimeError("boom")


@pytest.fixture()
def show(db):
    with db.session() as s:
        sh = Show(name="Hamlet")
        s.add(sh)
        s.commit()
        s.refresh(sh)
        L.ensure_layers(s, sh.id)
        return sh.id


def test_ensure_seeds_defaults_once(db):
    with db.session() as s:
        s.add(Show(name="X"))
        s.commit()
    with db.session() as s:
        sid = s.exec(select(Show)).first().id
        L.ensure_layers(s, sid)
        L.ensure_layers(s, sid)
        names = [r["name"] for r in L.get_layers(s, sid)]
    assert names == ["Lights", "Sound", "Video", "Audio", "Stage"]


def test_builtin_types_only_without_plugins():
    types = L.get_layer_types(None)
    assert set(types) == set(L.BUILTIN_LAYER_TYPES)
    assert "mix" not in types


def test_plugin_type_and_layer_are_seeded(db):
    pm = _pm(TheatreMix())
    with db.session() as s:
        sh = Show(name="Mixed")
        s.add(sh)
        s.commit()
        s.refresh(sh)
        L.ensure_layers(s, sh.id, pm)
        layers = L.get_layers(s, sh.id, pm)
    assert "mix" in L.get_layer_types(pm)
    assert layers[-1] == {
        "id": layers[-1]["id"],
        "name": "Mix",
        "color": "pink",
        "type": "mix",
        "sequence": 5,
        "available": True,
    }


def test_duplicate_type_first_wins_and_bad_registrations_are_skipped(db, caplog):
    class Dup:
        @showrunner.hookimpl
        def showrunner_get_layer_types(self):
            return [
                {"type": "lights", "label": "Hijack", "color": "red"},
                {"type": "mix", "label": "Mix"},
            ]

    pm = _pm(TheatreMix(), Dup(), Broken())
    types = L.get_layer_types(pm)
    assert (
        types["lights"]["label"] == "Lights"
    )  # built-in kept over the plugin's duplicate
    assert (
        types["mix"]["label"] == "Mix"
    )  # TheatreMix registered first, so it wins over Dup's 'mix'
    assert len(types) == len(L.BUILTIN_LAYER_TYPES) + 1  # Broken adds nothing


def test_plugin_layer_does_not_overwrite_user_edit(db, show):
    pm = _pm(TheatreMix())
    with db.session() as s:
        L.add_layer(s, show, "Mix", "generic", "blue")  # user made their own Mix
        L.ensure_layers(s, show, pm)
        rows = L.get_layers(s, show, pm)
    m = [r for r in rows if r["name"] == "Mix"]
    assert len(m) == 1 and m[0]["color"] == "blue" and m[0]["type"] == "generic"


def test_unknown_type_rejected_on_create(db, show):
    with db.session() as s:
        with pytest.raises(ValueError, match="unknown layer type"):
            L.add_layer(
                s, show, "Fog", "mix", "grey"
            )  # mix not registered without the plugin


def test_orphaned_type_is_kept_and_flagged(db, show):
    with db.session() as s:
        L.add_layer(s, show, "Fog", "mix", "grey", pm=_pm(TheatreMix()))
        rows = L.get_layers(s, show, pm=None)
    fog = [r for r in rows if r["name"] == "Fog"][0]
    assert fog["available"] is False and fog["color"] == "grey"


def test_rename_cascades_to_cues(db, show):
    with db.session() as s:
        cl = CueList(show_id=show, name="Main")
        s.add(cl)
        s.commit()
        s.refresh(cl)
        s.add(Cue(cue_list_id=cl.id, number="1", layer="Lights"))
        s.commit()
        lights = s.exec(select(Layer).where(Layer.name == "Lights")).one()
        L.update_layer(s, lights, name="Lamps")
        names = [c.layer for c in s.exec(select(Cue)).all()]
    assert names == ["Lamps"]


def test_delete_blocked_when_in_use(db, show):
    with db.session() as s:
        cl = CueList(show_id=show, name="Main")
        s.add(cl)
        s.commit()
        s.refresh(cl)
        s.add(Cue(cue_list_id=cl.id, number="1", layer="Sound"))
        s.commit()
        sound = s.exec(select(Layer).where(Layer.name == "Sound")).one()
        with pytest.raises(ValueError, match="used by 1 cue"):
            L.delete_layer(s, sound)
        video = s.exec(select(Layer).where(Layer.name == "Video")).one()
        L.delete_layer(s, video)
        assert "Video" not in [r["name"] for r in L.get_layers(s, show)]


def test_bad_colour_and_duplicate_name_rejected(db, show):
    with db.session() as s:
        with pytest.raises(ValueError, match="invalid colour"):
            L.add_layer(s, show, "Fog", "generic", 'red"><script')
        with pytest.raises(ValueError, match="already exists"):
            L.add_layer(s, show, "Lights", "lights", "orange")


def test_pdf_cue_colour_uses_layer_colour_then_toml_override():
    from showrunner.plugins.printer import _cue_color

    colours = {'Lights': 'orange', 'Mix': 'pink'}
    assert _cue_color({}, 'Lights', colours) == (255, 165, 0)
    assert _cue_color({}, 'Mix', colours) == (233, 30, 99)
    assert _cue_color({}, 'Gone', colours) == (128, 128, 128)  # unknown layer falls back to grey
    layout = {'cues': {'colors': {'Lights': [1, 2, 3]}}}
    assert _cue_color(layout, 'Lights', colours) == (1, 2, 3)  # TOML override wins


@pytest.fixture()
def cli_show(tmp_path, monkeypatch):
    """CLI pointed at a temp DB with one show (id 1) seeded with default layers."""
    from showrunner import cli as cli_mod

    path = tmp_path / 'cli.db'
    db = ShowDatabase(path)
    db.create_schema()
    with db.session() as s:
        s.add(Show(name='Hamlet'))
        s.commit()
        L.ensure_layers(s, 1)
        s.add(CueList(show_id=1, name='Main'))
        s.commit()
    monkeypatch.setattr(cli_mod, '_db', lambda: ShowDatabase(path))
    monkeypatch.setattr(cli_mod, '_pm', lambda: None)
    return cli_mod


def _run(cli_mod, *args):
    from typer.testing import CliRunner

    return CliRunner().invoke(cli_mod.cli, list(args))


def test_cli_layer_add_set_remove(cli_show):
    assert _run(cli_show, 'layers', 'add', 'FX', '--type', 'video', '--color', 'pink',
                '--show', '1').exit_code == 0
    assert 'FX' in _run(cli_show, 'layers', 'list', '--show', '1').output
    assert _run(cli_show, 'layers', 'set', 'FX', '--name', 'FX2', '--show', '1').exit_code == 0
    assert _run(cli_show, 'layers', 'remove', 'FX2', '--show', '1').exit_code == 0


def test_cli_rejects_unknown_type_and_unknown_cue_layer(cli_show):
    r = _run(cli_show, 'layers', 'add', 'Mix', '--type', 'mix', '--show', '1')
    assert r.exit_code == 1 and "unknown layer type" in r.output
    r = _run(cli_show, 'cues', 'add', '1', 'Go', '--layer', 'Nope')
    assert r.exit_code == 1 and 'not in this show' in r.output
    assert _run(cli_show, 'cues', 'add', '1', 'Go', '--layer', 'Lights').exit_code == 0


def test_cli_remove_refused_while_cue_uses_layer(cli_show):
    _run(cli_show, 'cues', 'add', '1', 'Go', '--layer', 'Lights')
    r = _run(cli_show, 'layers', 'remove', 'Lights', '--show', '1')
    assert r.exit_code == 1 and 'used by 1 cue' in r.output


def test_layer_routes(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import showrunner.plugins.db as db_module

    test_db = ShowDatabase(tmp_path / 'routes.db')
    test_db.create_schema()
    monkeypatch.setattr(db_module, '_db', test_db)
    monkeypatch.setattr(db_module, '_pm', None)
    app = FastAPI()
    app.include_router(db_module.router)
    with TestClient(app) as c:
        show = c.post('/db/shows?name=Hamlet').json()
        sid = show['id']
        types = {t['type'] for t in c.get('/db/layer-types').json()}
        assert {'generic', 'lights', 'sound'} <= types
        assert len(c.get(f'/db/shows/{sid}/layers').json()) == 5
        assert c.post(f'/db/shows/{sid}/layers?name=Mix&type=mix').status_code == 422
        assert c.post(f'/db/shows/{sid}/layers?name=FX&type=generic&color=pink').status_code == 200
        assert c.get('/db/shows/999/layers').status_code == 404
        lid = next(r['id'] for r in c.get(f'/db/shows/{sid}/layers').json() if r['name'] == 'FX')
        assert c.put(f'/db/layers/{lid}?color=bad%20colour').status_code == 422
        assert c.put(f'/db/layers/{lid}?name=FX2').json()['name'] == 'FX2'
        assert c.delete(f'/db/layers/{lid}').status_code == 200
        assert c.delete(f'/db/layers/{lid}').status_code == 404
