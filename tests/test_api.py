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
