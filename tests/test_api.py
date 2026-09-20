"""Tests for the ShowRunner HTTP API routes (ShowDB plugin).

Uses a minimal FastAPI app with the db router injected with a temporary
database, so no full ShowRunner startup is required.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import showrunner.plugins.db as db_module
from showrunner.database import ShowDatabase
from showrunner.plugins.db import router


@pytest.fixture()
def client(tmp_path):
    """Create a TestClient wired to a fresh temporary database."""
    test_db = ShowDatabase(tmp_path / 'api_test.db')
    test_db.create_schema()

    # Inject the test database into the module-level reference used by routes.
    db_module._db = test_db

    app = FastAPI()
    app.include_router(router)

    with TestClient(app) as c:
        yield c

    db_module._db = None
    test_db.close()


# ---------------------------------------------------------------------------
# GET /db/shows
# ---------------------------------------------------------------------------


def test_list_shows_returns_empty_list(client):
    response = client.get('/db/shows')
    assert response.status_code == 200
    assert response.json() == []


def test_list_shows_returns_all_shows(client):
    client.post('/db/shows?name=Alpha')
    client.post('/db/shows?name=Zebra')
    response = client.get('/db/shows')
    assert response.status_code == 200
    assert len(response.json()) == 2


def test_list_shows_ordered_by_name(client):
    client.post('/db/shows?name=Zebra')
    client.post('/db/shows?name=Alpha')
    client.post('/db/shows?name=Midsummer')
    names = [s['name'] for s in client.get('/db/shows').json()]
    assert names == sorted(names)


# ---------------------------------------------------------------------------
# POST /db/shows
# ---------------------------------------------------------------------------


def test_create_show_returns_show_data(client):
    response = client.post('/db/shows?name=My+Show')
    assert response.status_code == 200
    data = response.json()
    assert data['name'] == 'My Show'
    assert data['id'] is not None


def test_create_show_with_venue(client):
    response = client.post('/db/shows?name=Hamlet&venue=Globe+Theatre')
    assert response.status_code == 200
    data = response.json()
    assert data['name'] == 'Hamlet'
    assert data['venue'] == 'Globe Theatre'


def test_create_show_without_venue(client):
    response = client.post('/db/shows?name=Wicked')
    assert response.status_code == 200
    assert response.json()['venue'] is None


def test_created_show_appears_in_list(client):
    client.post('/db/shows?name=New+Show')
    shows = client.get('/db/shows').json()
    assert any(s['name'] == 'New Show' for s in shows)


# ---------------------------------------------------------------------------
# GET /db/shows/{show_id}
# ---------------------------------------------------------------------------


def test_get_show_returns_show(client):
    created = client.post('/db/shows?name=Test').json()
    response = client.get(f'/db/shows/{created["id"]}')
    assert response.status_code == 200
    assert response.json()['name'] == 'Test'


def test_get_show_not_found_returns_404(client):
    """This verifies the HTTPException fix — old code returned 200 with a JSON array."""
    response = client.get('/db/shows/9999')
    assert response.status_code == 404


def test_get_show_not_found_has_detail_key(client):
    response = client.get('/db/shows/9999')
    body = response.json()
    assert 'detail' in body


def test_get_show_not_found_body_is_not_array(client):
    """Confirm the response is a JSON object, not an array."""
    response = client.get('/db/shows/9999')
    assert not isinstance(response.json(), list)


# ---------------------------------------------------------------------------
# GET /db/shows/{show_id}/cues
# ---------------------------------------------------------------------------


def test_list_cues_empty_for_new_show(client):
    show = client.post('/db/shows?name=Cue+Show').json()
    response = client.get(f'/db/shows/{show["id"]}/cues')
    assert response.status_code == 200
    assert response.json() == []


def test_list_cues_filtered_by_cue_list_id(client):
    """?cue_list_id= must only return cues from that list."""
    import showrunner.plugins.db as db_module
    from showrunner.models import Cue, CueList

    show = client.post('/db/shows?name=Filter+Show').json()
    show_id = show['id']

    db = db_module._db
    with db.session() as s:
        list_a = CueList(show_id=show_id, name='A')
        list_b = CueList(show_id=show_id, name='B')
        s.add_all([list_a, list_b])
        s.flush()
        s.add(Cue(cue_list_id=list_a.id, number=1, name='A-cue'))
        s.add(Cue(cue_list_id=list_b.id, number=1, name='B-cue'))
        s.commit()
        list_a_id = list_a.id
        list_b_id = list_b.id

    resp_a = client.get(f'/db/shows/{show_id}/cues?cue_list_id={list_a_id}')
    assert resp_a.status_code == 200
    names_a = [c['name'] for c in resp_a.json()]
    assert names_a == ['A-cue']

    resp_b = client.get(f'/db/shows/{show_id}/cues?cue_list_id={list_b_id}')
    names_b = [c['name'] for c in resp_b.json()]
    assert names_b == ['B-cue']


def test_list_cues_unfiltered_returns_all_lists(client):
    import showrunner.plugins.db as db_module
    from showrunner.models import Cue, CueList

    show = client.post('/db/shows?name=All+Cues+Show').json()
    show_id = show['id']

    db = db_module._db
    with db.session() as s:
        list_a = CueList(show_id=show_id, name='A')
        list_b = CueList(show_id=show_id, name='B')
        s.add_all([list_a, list_b])
        s.flush()
        s.add(Cue(cue_list_id=list_a.id, number=1, name='A-cue'))
        s.add(Cue(cue_list_id=list_b.id, number=1, name='B-cue'))
        s.commit()

    resp = client.get(f'/db/shows/{show_id}/cues')
    assert resp.status_code == 200
    assert len(resp.json()) == 2


# ---------------------------------------------------------------------------
# GET /db/shows/{show_id}/cue-lists
# ---------------------------------------------------------------------------


def test_list_cue_lists_empty_for_new_show(client):
    show = client.post('/db/shows?name=CL+Show').json()
    response = client.get(f'/db/shows/{show["id"]}/cue-lists')
    assert response.status_code == 200
    assert response.json() == []


def test_list_cue_lists_returns_all_lists(client):
    import showrunner.plugins.db as db_module
    from showrunner.models import CueList

    show = client.post('/db/shows?name=Multi+CL+Show').json()
    show_id = show['id']

    db = db_module._db
    with db.session() as s:
        s.add(CueList(show_id=show_id, name='RECORDING'))
        s.add(CueList(show_id=show_id, name='AUDIO'))
        s.add(CueList(show_id=show_id, name='PLAYBACK'))
        s.commit()

    response = client.get(f'/db/shows/{show_id}/cue-lists')
    assert response.status_code == 200
    names = [cl['name'] for cl in response.json()]
    assert set(names) == {'RECORDING', 'AUDIO', 'PLAYBACK'}


def test_list_cue_lists_ordered_by_id(client):
    import showrunner.plugins.db as db_module
    from showrunner.models import CueList

    show = client.post('/db/shows?name=Order+Show').json()
    show_id = show['id']

    db = db_module._db
    with db.session() as s:
        for name in ('FIRST', 'SECOND', 'THIRD'):
            s.add(CueList(show_id=show_id, name=name))
        s.commit()

    response = client.get(f'/db/shows/{show_id}/cue-lists')
    ids = [cl['id'] for cl in response.json()]
    assert ids == sorted(ids)


def test_list_cue_lists_404_for_missing_show(client):
    response = client.get('/db/shows/9999/cue-lists')
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /db/cues/{cue_id}
# ---------------------------------------------------------------------------


@pytest.fixture()
def cue_in_db(client):
    """Create a show → cue list → cue and return the cue id."""
    import showrunner.plugins.db as db_module
    from showrunner.models import Cue, CueList

    show = client.post('/db/shows?name=Patch+Show').json()
    db = db_module._db
    with db.session() as s:
        cl = CueList(show_id=show['id'], name='Main')
        s.add(cl)
        s.flush()
        cue = Cue(cue_list_id=cl.id, number=1, name='Original Name', layer='Lights')
        s.add(cue)
        s.commit()
        return cue.id


def test_patch_cue_updates_name(client, cue_in_db):
    resp = client.patch(f'/db/cues/{cue_in_db}', json={'name': 'Updated Name'})
    assert resp.status_code == 200
    assert resp.json()['name'] == 'Updated Name'


def test_patch_cue_updates_notes(client, cue_in_db):
    notes = '{"osc": {"address": "/ardour/transport_play", "args": []}}'
    resp = client.patch(f'/db/cues/{cue_in_db}', json={'notes': notes})
    assert resp.status_code == 200
    assert resp.json()['notes'] == notes


def test_patch_cue_partial_update_preserves_other_fields(client, cue_in_db):
    """Updating name must not wipe the layer field."""
    resp = client.patch(f'/db/cues/{cue_in_db}', json={'name': 'New Name'})
    assert resp.json()['layer'] == 'Lights'


def test_patch_cue_updates_layer(client, cue_in_db):
    resp = client.patch(f'/db/cues/{cue_in_db}', json={'layer': 'Audio'})
    assert resp.status_code == 200
    assert resp.json()['layer'] == 'Audio'


def test_patch_cue_updates_color(client, cue_in_db):
    resp = client.patch(f'/db/cues/{cue_in_db}', json={'color': '#ff0000'})
    assert resp.status_code == 200
    assert resp.json()['color'] == '#ff0000'


def test_patch_cue_404_for_missing_cue(client):
    resp = client.patch('/db/cues/9999', json={'name': 'Ghost'})
    assert resp.status_code == 404


def test_patch_cue_422_for_empty_body(client, cue_in_db):
    resp = client.patch(f'/db/cues/{cue_in_db}', json={})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /db/shows/{show_id}/actors
# ---------------------------------------------------------------------------


def test_list_actors_empty_for_new_show(client):
    show = client.post('/db/shows?name=Actor+Show').json()
    response = client.get(f'/db/shows/{show["id"]}/actors')
    assert response.status_code == 200
    assert response.json() == []


# ---------------------------------------------------------------------------
# GET /db/shows/{show_id}/logs
# ---------------------------------------------------------------------------


def test_list_logs_empty_for_new_show(client):
    show = client.post('/db/shows?name=Log+Show').json()
    response = client.get(f'/db/shows/{show["id"]}/logs')
    assert response.status_code == 200
    assert response.json() == []


# ---------------------------------------------------------------------------
# GET /db/shows/{show_id}/config
# ---------------------------------------------------------------------------


def test_list_config_empty_for_new_show(client):
    show = client.post('/db/shows?name=Config+Show').json()
    response = client.get(f'/db/shows/{show["id"]}/config')
    assert response.status_code == 200
    assert response.json() == {}


def test_list_config_returns_dict(client):
    show = client.post('/db/shows?name=Dict+Show').json()
    result = client.get(f'/db/shows/{show["id"]}/config').json()
    assert isinstance(result, dict)
