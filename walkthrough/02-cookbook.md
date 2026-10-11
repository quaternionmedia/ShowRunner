# 02 — Cookbook

> **Runtime: hermetic.** Commands for somebody who has finished
> [01 — Onboarding](01-onboarding.md). No port and no browser; each example runs
> against a database in a temporary directory.

| To | Run |
|---|---|
| start the server and UI | `uv run sr start` (then <http://localhost:8000>) |
| start it with reload | `uv run python scripts/dev` |
| create a show | `uv run sr create Hamlet --venue "The Globe"` |
| list shows | `uv run sr list` |
| see the API | <http://localhost:8000/api> |
| run tests and these pages | `uv run pytest` |
| run the browser tests | `uv run pytest tests/e2e` |
| send each cue's `family` block to the apps | `uv run sr serve --port 8000 --state ./state --family showstopper=http://127.0.0.1:18411 --family-pull leo` |
| see what each cue delivered, and how long it took | `GET /family/` |
| read a pull member's action, as leo does | `GET /family/members/leo` |

## The CLI against a config of your own

`sr` reads `show.toml` from the directory it runs in:

```python
>>> import subprocess, sys, tempfile
>>> from pathlib import Path
>>> here = Path(tempfile.mkdtemp())
>>> _ = (here / 'show.toml').write_text('[database]\npath = "show.db"\n')
>>> def sr(*args):
...     return subprocess.run([sys.executable, '-m', 'showrunner.cli', *args], cwd=here,
...                           capture_output=True, text=True, check=True).stdout
>>> print(sr('create', 'Hamlet', '--venue', 'The Globe').splitlines()[-1])
Created show "Hamlet" (id=1)
>>> 'The Globe' in sr('list')
True

```

## The REST API, in process

The ShowDB routes on a `TestClient`, which is how `tests/test_api.py` exercises
them:

```python
>>> from fastapi import FastAPI
>>> from fastapi.testclient import TestClient
>>> import showrunner.plugins.db as showdb
>>> from showrunner.database import ShowDatabase
>>> showdb._db = ShowDatabase(here / 'api.db')
>>> showdb._db.create_schema()
>>> api = FastAPI()
>>> api.include_router(showdb.router)
>>> client = TestClient(api)
>>> client.post('/db/shows', params={'name': 'Pirates of Penzance'}).json()['id']
1
>>> [show['name'] for show in client.get('/db/shows').json()]
['Pirates of Penzance']
>>> client.get('/db/shows/99').status_code
404
>>> showdb._db.close()
>>> showdb._db = None

```

## The family routes, in process

A cue whose `notes` carry a `family` block is delivered when it fires: a push
member is sent `POST <its url>/family/cue`, and a pull member reads its action
from the hub. `GET /family/` lists every delivery's outcome. The pull side,
with no port:

```python
>>> import showrunner.plugins.family as family
>>> family_api = FastAPI()
>>> family_api.include_router(family.router)
>>> hub = TestClient(family_api)
>>> _ = family.configure({'pull': ['leo']})
>>> hub.get('/family/members/leo').json()
{'cue': None, 'action': None}
>>> cue = {'number': '3', 'name': 'Second tune', 'fired_at': '2026-10-10T21:00:00+00:00'}
>>> family._family.fire(cue, {'leo': {'song': 'Blue Bossa'}})
['leo']
>>> hub.get('/family/members/leo').json()['action']
{'song': 'Blue Bossa'}
>>> [(o['member'], o['via'], o['status']) for o in hub.get('/family/').json()['last']]
[('leo', 'pull', 200)]
>>> family.ShowFamilyPlugin().showrunner_shutdown(None)

```
